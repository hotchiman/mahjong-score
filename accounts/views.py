from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, Avg, Sum, Count
from django.db import transaction
from decimal import Decimal, ROUND_HALF_UP
import json

from .models import (
    MahjongRule, GameRoom, GameRoomMember,
    GameSession, RoundResult, PlayerRoundResult, RoundYaku,
    RoundMaster, YakuMaster, GameResult,
)

SEATS = ['east', 'south', 'west', 'north']
SEAT_LABEL = {'east': '起家', 'south': '南家', 'west': '西家', 'north': '北家'}


# ── デコレータ ──────────────────────────────────────

def superuser_required(view_func):
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            messages.error(request, 'この操作はスーパーユーザのみ実行できます。')
            return redirect('game_list')
        return view_func(request, *args, **kwargs)
    return wrapped


# ── pts 算出ロジック ────────────────────────────────

def calc_pts_with_rule(last_points, seat_users, rule):
    """
    ルール設定に基づきptsと着順を返す。
    戻り値: { seat: {'rank': int, 'pts': Decimal} }
    """
    D = Decimal
    init_pts   = D(rule.init_points)
    return_pts = D(rule.return_points)
    uma        = [D(rule.uma1), D(rule.uma2), D(rule.uma3), D(rule.uma4)]
    draw_mode  = rule.draw_handling  # 'split_east' | 'split' | 'east_priority'

    pts_raw  = {seat: D(last_points[seat]) for seat in SEATS}

    # ① 起家優先で仮着順を決める
    def seat_priority(seat): return SEATS.index(seat)
    ranked = sorted(SEATS, key=lambda s: (-pts_raw[s], seat_priority(s)))

    # ② 仮着順でptsを算出
    def base_pts(seat, rank):
        # (最終持ち点 - 返し点) / 1000 + ウマ
        p = (pts_raw[seat] - return_pts) / 1000 + uma[rank - 1]
        # 1着ボーナス: (返し点 - 初期持ち点) * 4 / 1000
        if rank == 1:
            p += (return_pts - init_pts) * 4 / 1000
        return p

    result = {}

    if draw_mode == 'east_priority':
        # 起家優先：同点でも上のロジックで確定
        for rank, seat in enumerate(ranked, 1):
            result[seat] = {'rank': rank, 'pts': base_pts(seat, rank).quantize(D('0.1'), rounding=ROUND_HALF_UP)}

    elif draw_mode in ('split', 'split_east'):
        # まず仮ptsを計算
        tmp = {}
        for rank, seat in enumerate(ranked, 1):
            tmp[seat] = {'rank': rank, 'pts': base_pts(seat, rank)}

        # 同点グループを検出して同着にする
        # グループ = 同じ最終持ち点を持つ座席の集合
        done = set()
        for seat in SEATS:
            if seat in done:
                continue
            same = [s for s in SEATS if pts_raw[s] == pts_raw[seat]]
            if len(same) == 1:
                result[seat] = {
                    'rank': tmp[seat]['rank'],
                    'pts': tmp[seat]['pts'].quantize(D('0.1'), rounding=ROUND_HALF_UP)
                }
                done.add(seat)
                continue

            # 同着グループ: 最上位の仮着順を同着着順とする
            top_rank = min(tmp[s]['rank'] for s in same)
            total_pts = sum(tmp[s]['pts'] for s in same)
            count = len(same)

            if draw_mode == 'split':
                # 均等分け（四捨五入）
                avg = (total_pts / count).quantize(D('0.1'), rounding=ROUND_HALF_UP)
                for s in same:
                    result[s] = {'rank': top_rank, 'pts': avg}

            else:  # split_east
                # 0.1 pt 単位の商とあまりを起家優先で加算
                unit = D('0.1')
                quotient = int(total_pts / unit) // count  # 小数第一位まで商
                remainder = int(total_pts / unit) - quotient * count  # あまり（0.1 pts 単位）
                # 座席順（起家優先）でグループを並べる
                same_sorted = sorted(same, key=lambda s: SEATS.index(s))
                for idx, s in enumerate(same_sorted):
                    extra = unit if idx < remainder else D('0')
                    result[s] = {'rank': top_rank, 'pts': D(quotient) * unit + extra}

            done.update(same)
    else:
        for rank, seat in enumerate(ranked, 1):
            result[seat] = {'rank': rank, 'pts': base_pts(seat, rank).quantize(D('0.1'), rounding=ROUND_HALF_UP)}

    return result


# ── 認証 ────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return redirect('game_list')
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        if not username or not password:
            messages.error(request, 'ユーザ名とパスワードを入力してください。')
        else:
            user = authenticate(request, username=username, password=password)
            if user:
                login(request, user)
                return redirect('game_list')
            else:
                messages.error(request, 'ユーザ名またはパスワードが正しくありません。')
    return render(request, 'accounts/login.html')


def logout_view(request):
    logout(request)
    return redirect('login')


def register_view(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        password_confirm = request.POST.get('password_confirm', '')
        def username_width(s):
            """半角=1、全角=2 として合計幅を返す"""
            import unicodedata
            return sum(2 if unicodedata.east_asian_width(ch) in ('W', 'F', 'A') else 1 for ch in s)

        error = None
        if not username or not password:
            error = 'ユーザ名とパスワードを入力してください。'
        elif username_width(username) > 10:
            error = 'ユーザ名は半角10文字以内（全角5文字以内）で入力してください。'
        elif password != password_confirm:
            error = 'パスワードが一致しません。'
        elif User.objects.filter(username=username).exists():
            error = 'このユーザ名は既に使用されています。'
        elif len(password) < 4:
            error = 'パスワードは4文字以上で入力してください。'
        if error:
            messages.error(request, error)
        else:
            User.objects.create_user(username=username, password=password)
            messages.success(request, f'ユーザ「{username}」を登録しました。')
            return redirect('login')
    return render(request, 'accounts/register.html')


# ── ユーザ管理 ──────────────────────────────────────

@superuser_required
def user_list_view(request):
    users = User.objects.all().order_by('id')
    return render(request, 'accounts/user_list.html', {'users': users})


@login_required
def user_edit_view(request, user_id):
    target_user = get_object_or_404(User, pk=user_id)
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        password_confirm = request.POST.get('password_confirm', '')
        error = None
        if not username:
            error = 'ユーザ名を入力してください。'
        elif User.objects.filter(username=username).exclude(pk=user_id).exists():
            error = 'このユーザ名は既に使用されています。'
        elif password and password != password_confirm:
            error = 'パスワードが一致しません。'
        elif password and len(password) < 4:
            error = 'パスワードは4文字以上で入力してください。'
        if error:
            messages.error(request, error)
        else:
            target_user.username = username
            if password:
                target_user.set_password(password)
            target_user.save()
            messages.success(request, 'ユーザ情報を更新しました。')
            return redirect('user_list')
    return render(request, 'accounts/user_edit.html', {'target_user': target_user})


@superuser_required
def user_delete_view(request, user_id):
    target_user = get_object_or_404(User, pk=user_id)
    if request.method == 'POST':
        username = target_user.username
        is_self = (request.user.pk == target_user.pk)
        target_user.delete()
        if is_self:
            logout(request)
            return redirect('login')
        messages.success(request, f'ユーザ「{username}」を削除しました。')
        return redirect('user_list')
    return render(request, 'accounts/user_delete.html', {'target_user': target_user})


# ── 対局ルーム ──────────────────────────────────────

@login_required
def game_list_view(request):
    # スーパーユーザは全対局を表示、それ以外は自分が作成/参加している対局のみ
    if request.user.is_superuser:
        rooms = GameRoom.objects.all().select_related('rule', 'created_by').prefetch_related('members')
    else:
        rooms = GameRoom.objects.filter(
            Q(created_by=request.user) | Q(members__user=request.user)
        ).distinct().select_related('rule', 'created_by').prefetch_related('members')

    # 各ルームの対局結果サマリを付加（自分の成績を表示）
    rooms_with_stats = []
    for room in rooms:
        if request.user.is_superuser:
            # スーパーユーザは全参加者合計を表示
            results = GameResult.objects.filter(session__room=room)
        else:
            # 一般ユーザは自分の成績のみ
            results = GameResult.objects.filter(session__room=room, user=request.user)

        total_pts = results.aggregate(s=Sum('pts'))['s']
        avg_rank  = results.aggregate(a=Avg('rank'))['a']
        finalized_count = room.sessions.filter(is_finalized=True).count()
        rooms_with_stats.append({
            'room': room,
            'total_pts': total_pts,
            'avg_rank': round(avg_rank, 2) if avg_rank else None,
            'finalized_count': finalized_count,
        })

    return render(request, 'accounts/game_list.html', {'rooms_with_stats': rooms_with_stats})


@login_required
def game_create_view(request):
    rules = MahjongRule.objects.all()
    if request.method == 'POST':
        name       = request.POST.get('name', '').strip()
        game_count = request.POST.get('game_count', '').strip()
        rule_id    = request.POST.get('rule', '')
        error = None
        if not name:
            error = '対局名を入力してください。'
        elif not game_count or not game_count.isdigit() or int(game_count) < 1:
            error = '対局数は1以上の整数を入力してください。'
        elif not rule_id:
            error = 'ルールを選択してください。'
        if error:
            messages.error(request, error)
        else:
            rule = get_object_or_404(MahjongRule, pk=rule_id)
            room = GameRoom.objects.create(
                name=name, game_count=int(game_count), rule=rule, created_by=request.user
            )
            GameRoomMember.objects.create(room=room, user=request.user)
            messages.success(request, f'対局ルーム「{name}」を作成しました。')
            return redirect('game_list')
    return render(request, 'accounts/game_create.html', {'rules': rules})


@login_required
def game_join_view(request, token):
    room = get_object_or_404(GameRoom, invite_token=token)
    already_joined = GameRoomMember.objects.filter(room=room, user=request.user).exists()
    members = room.members.select_related('user').all()
    if request.method == 'POST':
        if not already_joined:
            GameRoomMember.objects.create(room=room, user=request.user)
            messages.success(request, f'対局ルーム「{room.name}」に参加しました。')
            return redirect('game_list')
    return render(request, 'accounts/game_join.html', {
        'room': room, 'members': members, 'already_joined': already_joined
    })


@superuser_required
def game_delete_view(request, room_id):
    room = get_object_or_404(GameRoom, pk=room_id)
    if request.method == 'POST':
        name = room.name
        room.delete()
        messages.success(request, f'対局ルーム「{name}」を削除しました。')
        return redirect('game_list')
    return render(request, 'accounts/game_delete.html', {'room': room})


# ── 対局結果一覧（機能9） ───────────────────────────

@login_required
def game_result_list_view(request, room_id):
    room = get_object_or_404(GameRoom, pk=room_id)

    # prefetch_related から 'results' を外し、テンプレート側で直接アクセスしない形に
    sessions = list(room.sessions.select_related(
        'player_east', 'player_south', 'player_west', 'player_north'
    ).order_by('session_number'))

    members = [m.user for m in room.members.select_related('user')]

    summary = []
    for user in members:
        results = GameResult.objects.filter(session__room=room, user=user)
        total_pts = results.aggregate(s=Sum('pts'))['s'] or Decimal('0')
        avg_rank  = results.aggregate(a=Avg('rank'))['a']
        count     = results.count()
        details   = list(results.select_related('session').order_by('session__session_number'))
        # アコーディオン用に対局日も付加
        details_with_date = []
        for d in details:
            details_with_date.append({
                'session_number': d.session.session_number,
                'game_date': d.session.game_date,
                'rank': d.rank,
                'pts': d.pts,
            })
        summary.append({
            'user': user,
            'total_pts': total_pts,
            'avg_rank': round(avg_rank, 2) if avg_rank else None,
            'count': count,
            'details': details_with_date,
        })
    summary.sort(key=lambda x: x['total_pts'], reverse=True)

    # セッションごとの各プレイヤー詳細統計
    sessions_detail = []
    for sess in sessions:
        player_stats = []
        for seat in SEATS:
            player = getattr(sess, f'player_{seat}', None)
            if player is None:
                continue
            gr = GameResult.objects.filter(session=sess, user=player).first()
            prr_qs = PlayerRoundResult.objects.filter(
                round_result__session=sess, user=player
            )
            riichi_count = prr_qs.filter(is_riichi=True).count()
            agari_count  = prr_qs.filter(is_agari=True).count()
            houjuu_count = prr_qs.filter(is_houjuu=True).count()
            player_stats.append({
                'seat': seat,
                'seat_label': SEAT_LABEL[seat],
                'user': player,
                'rank': gr.rank if gr else None,
                'pts': gr.pts if gr else None,
                'final_points': gr.final_points if gr else None,
                'riichi_count': riichi_count,
                'agari_count': agari_count,
                'houjuu_count': houjuu_count,
            })
        # 着順でソート（確定済みの場合）
        player_stats_by_rank = sorted(
            [p for p in player_stats if p['rank'] is not None],
            key=lambda x: (x['rank'], SEATS.index(x['seat']))
        )
        sessions_detail.append({
            'session': sess,
            'player_stats': player_stats,
            'player_stats_by_rank': player_stats_by_rank,
        })

    return render(request, 'accounts/game_result_list.html', {
        'room': room, 'sessions': sessions,
        'sessions_detail': sessions_detail,
        'summary': summary, 'members': members,
        'can_edit': request.user.is_superuser or room.created_by == request.user,
    })


# ── 確定済み結果の編集・削除 ────────────────────────

def _can_edit_result(request, room):
    """スーパーユーザ または ルーム作成者のみ編集可"""
    return request.user.is_superuser or room.created_by == request.user


@login_required
def session_result_edit_view(request, room_id, session_id):
    """確定済みセッションの順位・pts を手動編集"""
    room    = get_object_or_404(GameRoom, pk=room_id)
    session = get_object_or_404(GameSession, pk=session_id, room=room)
    if not _can_edit_result(request, room):
        messages.error(request, '編集権限がありません。')
        return redirect('game_result_list', room_id=room_id)

    results = list(session.results.select_related('user').order_by('rank'))

    if request.method == 'POST':
        with transaction.atomic():
            for gr in results:
                rank_val = request.POST.get(f'rank_{gr.id}')
                pts_val  = request.POST.get(f'pts_{gr.id}')
                try:
                    gr.rank = int(rank_val)
                    gr.pts  = Decimal(pts_val)
                    gr.save()
                    setattr(session, f'rank_{gr.seat}', gr.rank)
                    setattr(session, f'pts_{gr.seat}',  gr.pts)
                except Exception:
                    pass
            session.save()
        messages.success(request, '対局結果を更新しました。')
        return redirect('game_result_list', room_id=room_id)

    return render(request, 'accounts/session_result_edit.html', {
        'room': room, 'session': session, 'results': results,
    })


@login_required
def session_result_delete_view(request, room_id, session_id):
    """確定済みセッションのデータを削除（セッションごと）"""
    room    = get_object_or_404(GameRoom, pk=room_id)
    session = get_object_or_404(GameSession, pk=session_id, room=room)
    if not _can_edit_result(request, room):
        messages.error(request, '削除権限がありません。')
        return redirect('game_result_list', room_id=room_id)

    if request.method == 'POST':
        session.delete()
        messages.success(request, f'対局{session.session_number}のデータを削除しました。')
        return redirect('game_result_list', room_id=room_id)

    return render(request, 'accounts/session_result_delete.html', {
        'room': room, 'session': session,
    })


# ── 対局結果登録（機能10） ──────────────────────────

def _session_context(room, session, members, rounds, yakus, readonly, session_number, existing_rounds=None):
    rule = room.rule
    rounds_json = json.dumps([{'id': r.id, 'name': r.name} for r in rounds])
    yakus_json  = json.dumps([{'id': y.id, 'name': y.name} for y in yakus])
    return {
        'room': room,
        'members': members,
        'rounds': rounds,
        'yakus': yakus,
        'session': session,
        'readonly': readonly,
        'session_number': session_number,
        'rounds_json': rounds_json,
        'yakus_json': yakus_json,
        'readonly_json': 'true' if readonly else 'false',
        'existing_rounds_json': json.dumps(existing_rounds) if existing_rounds is not None else 'null',
        'seats': [('east','起家'), ('south','南家'), ('west','西家'), ('north','北家')],
        'init_points': rule.init_points,
        'draw_handling': rule.draw_handling,
    }


@login_required
def session_create_view(request, room_id):
    room    = get_object_or_404(GameRoom, pk=room_id)
    next_no = (room.sessions.count() or 0) + 1
    members = [m.user for m in room.members.select_related('user')]
    rounds  = RoundMaster.objects.all()
    yakus   = YakuMaster.objects.all()
    if request.method == 'POST':
        return _save_session(request, room, None, next_no, members, rounds, yakus)
    return render(request, 'accounts/session_form.html',
                  _session_context(room, None, members, rounds, yakus, False, next_no))


@login_required
def session_edit_view(request, room_id, session_id):
    room    = get_object_or_404(GameRoom, pk=room_id)
    session = get_object_or_404(GameSession, pk=session_id, room=room)
    members = [m.user for m in room.members.select_related('user')]
    rounds  = RoundMaster.objects.all()
    yakus   = YakuMaster.objects.all()
    # スーパーユーザまたは対局ルームの作成者は確定済みでも編集可
    can_edit = _can_edit_result(request, room)
    readonly = session.is_finalized and not can_edit

    existing_rounds = []
    for rr in session.rounds.prefetch_related('player_results', 'yakus', 'yakus__yakus').order_by('round_number'):
        pr_map   = {pr.seat: pr for pr in rr.player_results.all()}
        yaku_obj = rr.yakus.first()
        existing_rounds.append({
            'round_number': rr.round_number,
            'round_master_id': rr.round_master_id,
            'honba': rr.honba,
            'kyotaku': rr.kyotaku,
            'is_ryukyoku': rr.is_ryukyoku,
            'pr': {seat: {
                'is_agari':       pr_map[seat].is_agari       if seat in pr_map else False,
                'agari_method':   pr_map[seat].agari_method   if seat in pr_map else '',
                'is_houjuu':      pr_map[seat].is_houjuu      if seat in pr_map else False,
                'is_furo':        pr_map[seat].is_furo        if seat in pr_map else False,
                'furo_count':     pr_map[seat].furo_count     if seat in pr_map else 0,
                'is_riichi':      pr_map[seat].is_riichi      if seat in pr_map else False,
                'ryukyoku_state': pr_map[seat].ryukyoku_state if seat in pr_map else '',
                'kyoku_balance':  pr_map[seat].kyoku_balance  if seat in pr_map else 0,
                'kyotaku_points': pr_map[seat].kyotaku_points if seat in pr_map else 0,
                'points_after':   pr_map[seat].points_after   if seat in pr_map else 0,
            } for seat in SEATS},
            'yaku': {
                'agari_user_id':  yaku_obj.agari_user_id  if yaku_obj else '',
                'houjuu_user_id': yaku_obj.houjuu_user_id if yaku_obj else '',
                'han':      yaku_obj.han      if yaku_obj else 0,
                'dora':     yaku_obj.dora     if yaku_obj else 0,
                'ura_dora': yaku_obj.ura_dora if yaku_obj else 0,
                'aka_dora': yaku_obj.aka_dora if yaku_obj else 0,
                'yaku_ids': [y.id for y in yaku_obj.yakus.all()] if yaku_obj else [],
            } if yaku_obj else None,
        })

    if request.method == 'POST' and not readonly:
        return _save_session(request, room, session, session.session_number, members, rounds, yakus)

    ctx = _session_context(room, session, members, rounds, yakus, readonly,
                           session.session_number, existing_rounds)
    ctx['can_edit_result'] = _can_edit_result(request, room)
    return render(request, 'accounts/session_form.html', ctx)


def _save_session(request, room, session, session_number, members, rounds, yakus):
    finalize  = 'finalize' in request.POST
    game_date = request.POST.get('game_date', '')
    seat_users = {}
    for seat in SEATS:
        uid = request.POST.get(f'player_{seat}')
        if uid:
            try:
                seat_users[seat] = User.objects.get(pk=uid)
            except User.DoesNotExist:
                pass

    if not game_date or len(seat_users) < 4:
        messages.error(request, '対局日と全座席のユーザを入力してください。')
        if session is not None:
            return redirect('session_edit', room_id=room.id, session_id=session.id)
        return redirect('session_create', room_id=room.id)

    with transaction.atomic():
        if session is None:
            session = GameSession.objects.create(
                room=room, session_number=session_number, game_date=game_date,
                player_east=seat_users['east'], player_south=seat_users['south'],
                player_west=seat_users['west'],  player_north=seat_users['north'],
            )
        else:
            session.game_date = game_date
            for seat in SEATS:
                setattr(session, f'player_{seat}', seat_users[seat])
            session.save()  # ← 既存セッションの基本情報を先に保存

        session.rounds.all().delete()
        round_count = int(request.POST.get('round_count', 0))
        last_points = {seat: 0 for seat in SEATS}

        for i in range(1, round_count + 1):
            rm_id = request.POST.get(f'round_{i}_master')
            # 局マスタ未選択の場合はその局をスキップ（ユーザが意図的に空欄にした行）
            if not rm_id:
                continue
            try:
                rm = RoundMaster.objects.get(pk=rm_id)
            except RoundMaster.DoesNotExist:
                continue
            honba       = int(request.POST.get(f'round_{i}_honba', 0) or 0)
            kyotaku     = int(request.POST.get(f'round_{i}_kyotaku', 0) or 0)
            is_ryukyoku = request.POST.get(f'round_{i}_ryukyoku') == '1'

            rr = RoundResult.objects.create(
                session=session, round_number=i, round_master=rm,
                honba=honba, kyotaku=kyotaku, is_ryukyoku=is_ryukyoku,
            )

            for seat in SEATS:
                p = seat_users.get(seat)
                if not p:
                    continue
                pf = f'round_{i}_{seat}'
                kyoku_balance  = int(request.POST.get(f'{pf}_balance', 0) or 0)
                kyotaku_points = int(request.POST.get(f'{pf}_kyotaku_pts', 0) or 0)
                points_after   = int(request.POST.get(f'{pf}_points_after', 0) or 0)
                last_points[seat] = points_after

                PlayerRoundResult.objects.create(
                    round_result=rr, user=p, seat=seat,
                    is_agari=request.POST.get(f'{pf}_agari') == '1',
                    agari_method=request.POST.get(f'{pf}_agari_method', ''),
                    is_houjuu=request.POST.get(f'{pf}_houjuu') == '1',
                    is_furo=request.POST.get(f'{pf}_furo') == '1',
                    furo_count=int(request.POST.get(f'{pf}_furo_count', 0) or 0),
                    is_riichi=request.POST.get(f'{pf}_riichi') == '1',
                    ryukyoku_state=request.POST.get(f'{pf}_ryukyoku_state', ''),
                    kyoku_balance=kyoku_balance,
                    kyotaku_points=kyotaku_points,
                    points_after=points_after,
                )

            agari_uid  = request.POST.get(f'round_{i}_yaku_agari')
            houjuu_uid = request.POST.get(f'round_{i}_yaku_houjuu')
            ry = RoundYaku.objects.create(
                round_result=rr,
                agari_user_id=agari_uid  if agari_uid  else None,
                houjuu_user_id=houjuu_uid if houjuu_uid else None,
                han=int(request.POST.get(f'round_{i}_yaku_han', 0) or 0),
                dora=int(request.POST.get(f'round_{i}_yaku_dora', 0) or 0),
                ura_dora=int(request.POST.get(f'round_{i}_yaku_ura', 0) or 0),
                aka_dora=int(request.POST.get(f'round_{i}_yaku_aka', 0) or 0),
            )
            yaku_ids = request.POST.getlist(f'round_{i}_yakus')
            if yaku_ids:
                ry.yakus.set(YakuMaster.objects.filter(pk__in=yaku_ids))

        if finalize and round_count > 0:
            rule = room.rule
            calc = calc_pts_with_rule(last_points, seat_users, rule)
            GameResult.objects.filter(session=session).delete()
            for seat in SEATS:
                rank = calc[seat]['rank']
                pts  = calc[seat]['pts']
                setattr(session, f'rank_{seat}', rank)
                setattr(session, f'pts_{seat}',  pts)
                GameResult.objects.create(
                    session=session, user=seat_users[seat], seat=seat,
                    rank=rank, pts=pts, final_points=last_points[seat],
                )
            session.is_finalized = True
            session.save()
        # finalize でない場合は既存セッション更新済み（上の session.save() 呼び済み）
        # 新規セッションの場合も GameSession.objects.create() で保存済み

    if finalize:
        messages.success(request, '対局を確定しました。')
        return redirect('game_result_list', room_id=room.id)
    else:
        messages.success(request, '対局結果を保存しました。')
        # 保存後は登録画面に留まる
        return redirect('session_edit', room_id=room.id, session_id=session.id)


# ── 対戦記録照会（機能11） ──────────────────────────

def _calc_user_stats(target_user, room_id, rule_id):
    """1ユーザ分の統計を計算して返す共通関数"""
    prr_qs = PlayerRoundResult.objects.filter(user=target_user)
    if room_id:
        prr_qs = prr_qs.filter(round_result__session__room_id=room_id)
    if rule_id:
        prr_qs = prr_qs.filter(round_result__session__room__rule_id=rule_id)

    prr_list      = list(prr_qs.select_related('round_result', 'round_result__session'))
    total_kyoku   = len(prr_list)
    agari_list    = [p for p in prr_list if p.is_agari]
    houjuu_list   = [p for p in prr_list if p.is_houjuu]
    tsumo_list    = [p for p in prr_list if p.is_agari and p.agari_method == 'tsumo']
    riichi_list   = [p for p in prr_list if p.is_riichi]
    furo_list     = [p for p in prr_list if p.is_furo]
    ryukyoku_list = [p for p in prr_list if p.round_result.is_ryukyoku]
    tenpai_list   = [p for p in prr_list if p.ryukyoku_state == 'tenpai']
    dama_list     = [p for p in agari_list if not p.is_riichi and not p.is_furo]
    riichi_agari   = [p for p in agari_list if p.is_riichi]
    riichi_houjuu  = [p for p in houjuu_list if p.is_riichi]
    riichi_ryukyoku= [p for p in ryukyoku_list if p.is_riichi]

    ippatsu_count = RoundYaku.objects.filter(
        round_result__in=[p.round_result for p in riichi_agari],
        agari_user=target_user, yakus__name='一発'
    ).count() if riichi_list else 0

    ura_agari = RoundYaku.objects.filter(
        round_result__in=[p.round_result for p in riichi_agari],
        agari_user=target_user, ura_dora__gte=1
    ).count() if riichi_agari else 0

    def safe_div(a, b): return round(a / b * 100, 2) if b else 0
    def safe_avg(lst, key): return round(sum(key(x) for x in lst) / len(lst)) if lst else 0

    agari_income  = safe_avg(agari_list,  lambda p: p.kyoku_balance + p.kyotaku_points)
    houjuu_loss   = safe_avg(houjuu_list, lambda p: abs(p.kyoku_balance + p.kyotaku_points))
    riichi_balance = round(sum(p.kyoku_balance + p.kyotaku_points for p in riichi_list) / len(riichi_list)) if riichi_list else 0
    riichi_income  = safe_avg(riichi_agari,  lambda p: p.kyoku_balance + p.kyotaku_points)
    riichi_loss    = safe_avg(riichi_houjuu, lambda p: abs(p.kyoku_balance + p.kyotaku_points))
    kyoku_balance_avg = round(sum(p.kyoku_balance + p.kyotaku_points for p in prr_list) / total_kyoku) if total_kyoku else 0

    session_ids  = set(p.round_result.session_id for p in prr_list)
    battle_count = GameResult.objects.filter(session_id__in=session_ids, user=target_user).count()
    avg_rank_val = GameResult.objects.filter(session_id__in=session_ids, user=target_user).aggregate(a=Avg('rank'))['a']

    # 着順分布
    gr_qs = GameResult.objects.filter(session_id__in=session_ids, user=target_user)
    rank_dist = {1: 0, 2: 0, 3: 0, 4: 0}
    for gr in gr_qs:
        if gr.rank in rank_dist:
            rank_dist[gr.rank] += 1

    total_pts = None
    if room_id or rule_id:
        pts_qs = GameResult.objects.filter(user=target_user)
        if room_id: pts_qs = pts_qs.filter(session__room_id=room_id)
        if rule_id: pts_qs = pts_qs.filter(session__room__rule_id=rule_id)
        total_pts = pts_qs.aggregate(s=Sum('pts'))['s'] or Decimal('0')

    打点効率     = round(safe_div(len(agari_list), total_kyoku) / 100 * agari_income)
    銃点損失     = round(safe_div(len(houjuu_list), total_kyoku) / 100 * houjuu_loss)
    調整打点効率 = 打点効率 - 銃点損失

    # 平均スコア（total_pts / battle_count）
    if total_pts is not None and battle_count > 0:
        avg_score = round(float(total_pts) / battle_count, 2)
    else:
        avg_score = None

    top_rate     = safe_div(rank_dist[1], battle_count)
    avoid4_rate  = safe_div(rank_dist[1] + rank_dist[2] + rank_dist[3], battle_count)
    sanka_rate   = round(safe_div(len(riichi_list), total_kyoku) + safe_div(len(furo_list), total_kyoku), 2)

    # 副露和了率、副露放銃率、平均副露回数、平均ドラ枚数、平均裏ドラ枚数、放銃時裏ドラ率、放銃時平均ドラ枚数、放銃時平均裏ドラ枚数
    def safe_avg2(lst, key): return round(sum(key(x) for x in lst) / len(lst), 2) if lst else 0

    furo_agari_list     = [p for p in prr_list if p.is_agari and p.is_furo]
    furo_houjuu_list    = [p for p in prr_list if p.is_houjuu and p.is_furo]
    furo_avg            = round(sum(p.furo_count for p in prr_list) / total_kyoku, 2) if total_kyoku else 0

    agari_dora_list = RoundYaku.objects.filter(
        round_result__in=[p.round_result for p in agari_list],
        agari_user=target_user
    )
    all_dora_avg        = safe_avg2(agari_dora_list,  lambda p: p.dora + p.ura_dora + p.aka_dora)
    ura_dora_avg        = round(sum(p.ura_dora for p in agari_dora_list) / len(riichi_agari), 2) if len(riichi_agari) else 0

    ura_houju = RoundYaku.objects.filter(
        round_result__in=[p.round_result for p in houjuu_list],
        houjuu_user=target_user, ura_dora__gte=1
    ).count() if houjuu_list else 0
    houjuu_dora_list = RoundYaku.objects.filter(
        round_result__in=[p.round_result for p in houjuu_list],
        houjuu_user=target_user
    )
    houjuu_ura_dora_list = RoundYaku.objects.filter(
        round_result__in=[p.round_result for p in houjuu_list],
        houjuu_user=target_user, yakus__name='立直'
    )
    houjuu_all_dora_avg = safe_avg2(houjuu_dora_list,  lambda p: p.dora + p.ura_dora + p.aka_dora)
    houjuu_ura_dora_avg = round(sum(p.ura_dora for p in houjuu_dora_list) / len(houjuu_ura_dora_list), 2) if len(houjuu_ura_dora_list) else 0


    return {
        'user':         target_user,
        'battle_count': battle_count,
        'total_kyoku':  total_kyoku,
        'total_pts':    total_pts,
        'avg_score':    avg_score,
        'agari_rate':   safe_div(len(agari_list), total_kyoku),
        'houjuu_rate':  safe_div(len(houjuu_list), total_kyoku),
        'tsumo_rate':   safe_div(len(tsumo_list), len(agari_list)),
        'dama_rate':    safe_div(len(dama_list), len(agari_list)),
        'ryukyoku_rate':safe_div(len(ryukyoku_list), total_kyoku),
        'tenpai_rate':  safe_div(len(tenpai_list), len(ryukyoku_list)),
        'furo_rate':    safe_div(len(furo_list), total_kyoku),
        'riichi_rate':  safe_div(len(riichi_list), total_kyoku),
        'sanka_rate':   sanka_rate,
        'avg_rank':     round(avg_rank_val, 3) if avg_rank_val else None,
        'agari_income': agari_income,
        'houjuu_loss':  houjuu_loss,
        'top_rate':     top_rate,
        'avoid4_rate':  avoid4_rate,
        'rank_dist':    f"{rank_dist[1]} - {rank_dist[2]} - {rank_dist[3]} - {rank_dist[4]}",
        'riichi_agari_rate':    safe_div(len(riichi_agari), len(riichi_list)),
        'riichi_houjuu_rate':   safe_div(len(riichi_houjuu), len(riichi_list)),
        'riichi_balance':       riichi_balance,
        'riichi_income':        riichi_income,
        'riichi_loss':          riichi_loss,
        'riichi_ryukyoku_rate': safe_div(len(riichi_ryukyoku), len(riichi_list)),
        'ippatsu_rate':         safe_div(ippatsu_count, len(riichi_list)),
        'ura_rate':             safe_div(ura_agari, len(riichi_agari)),
        'daten_efficiency':     打点効率,
        'juten_loss':           銃点損失,
        'adjusted_efficiency':  調整打点効率,
        'kyoku_balance_avg':    kyoku_balance_avg,
        'furo_agari_rate':      safe_div(len(furo_agari_list), len(furo_list)),
        'furo_houjuu_rate':     safe_div(len(furo_houjuu_list), len(furo_list)),
        'furo_avg':             furo_avg,
        'all_dora_avg':         all_dora_avg,
        'ura_dora_avg':         ura_dora_avg,
        'houjuu_ura_rate':      safe_div(ura_houju, len(houjuu_ura_dora_list)),
        'houjuu_all_dora_avg':  houjuu_all_dora_avg,
        'houjuu_ura_dora_avg':  houjuu_ura_dora_avg,
    }


@login_required
def battle_record_view(request):
    # 自分と同じ対局ルームに参加しているユーザのみ表示（スーパーユーザ除外）
    my_room_ids = GameRoom.objects.filter(
        Q(created_by=request.user) | Q(members__user=request.user)
    ).values_list('id', flat=True)

    users = User.objects.filter(
        is_superuser=False
    ).filter(
        Q(joined_rooms__room_id__in=my_room_ids) | Q(created_rooms__id__in=my_room_ids)
    ).distinct().order_by('username')

    rooms  = GameRoom.objects.filter(
        Q(created_by=request.user) | Q(members__user=request.user)
    ).distinct()
    rules  = MahjongRule.objects.all()

    # デフォルトはログインユーザ
    user_id = request.GET.get('user_id', str(request.user.id) if not request.user.is_superuser else '')
    room_id = request.GET.get('room_id', '')
    rule_id = request.GET.get('rule_id', '')

    target_user   = None
    stats         = None
    all_stats     = None   # All Players 用
    all_players   = (user_id == 'all')
    error_msg     = None

    if all_players:
        # All Players モード
        if not room_id and not rule_id:
            error_msg = 'All Players を選択した場合は、対局名もしくはルールを選択してください。'
        else:
            # 対象ユーザを収集（選択した room/rule に参加しているユーザ）
            target_users_qs = User.objects.filter(is_superuser=False)
            if room_id:
                target_users_qs = target_users_qs.filter(
                    Q(joined_rooms__room_id=room_id) | Q(created_rooms__id=room_id)
                ).distinct()
            if rule_id:
                target_users_qs = target_users_qs.filter(
                    Q(joined_rooms__room__rule_id=rule_id) | Q(created_rooms__rule_id=rule_id)
                ).distinct()

            rows = []
            for u in target_users_qs:
                s = _calc_user_stats(u, room_id, rule_id)
                if s['battle_count'] > 0:
                    rows.append(s)

            # ポイント降順ソート（total_pts が None の場合は最後）
            rows.sort(key=lambda x: float(x['total_pts']) if x['total_pts'] is not None else float('-inf'), reverse=True)
            # 順位付け
            for idx, row in enumerate(rows, 1):
                row['rank_no'] = idx
            all_stats = rows

    elif user_id:
        try:
            target_user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            pass

        if target_user:
            s = _calc_user_stats(target_user, room_id, rule_id)
            # 個人表示用に total_pts を stats に含める
            stats = s
            stats['total_pts'] = s['total_pts']  # 既に含まれている

    return render(request, 'accounts/battle_record.html', {
        'users': users, 'rooms': rooms, 'rules': rules,
        'target_user': target_user,
        'stats': stats,
        'all_stats': all_stats,
        'all_players': all_players,
        'error_msg': error_msg,
        'sel_user_id': user_id, 'sel_room_id': room_id, 'sel_rule_id': rule_id,
    })
