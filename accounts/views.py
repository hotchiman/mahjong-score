from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, Avg, Sum, Count
from django.db import transaction
from decimal import Decimal
import json

from .models import (
    MahjongRule, GameRoom, GameRoomMember,
    GameSession, RoundResult, PlayerRoundResult, RoundYaku,
    RoundMaster, YakuMaster, GameResult,
)

SEATS = ['east', 'south', 'west', 'north']
SEAT_LABEL = {'east': '起家', 'south': '南家', 'west': '西家', 'north': '北家'}
RANK_BONUS = {1: Decimal('20'), 2: Decimal('-20'), 3: Decimal('-40'), 4: Decimal('-60')}


def superuser_required(view_func):
    """スーパーユーザのみアクセス可能なデコレータ"""
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            messages.error(request, 'この操作はスーパーユーザのみ実行できます。')
            return redirect('game_list')
        return view_func(request, *args, **kwargs)
    return wrapped


# ──────────────── 認証 ────────────────

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
        error = None
        if not username or not password:
            error = 'ユーザ名とパスワードを入力してください。'
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


# ──────────────── ユーザ管理（スーパーユーザ限定） ────────────────

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


# ──────────────── 対局ルーム ────────────────

@login_required
def game_list_view(request):
    rooms = GameRoom.objects.filter(
        Q(created_by=request.user) | Q(members__user=request.user)
    ).distinct().select_related('rule', 'created_by').prefetch_related('members')
    return render(request, 'accounts/game_list.html', {'rooms': rooms})


@login_required
def game_create_view(request):
    rules = MahjongRule.objects.all()
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        game_count = request.POST.get('game_count', '').strip()
        rule_id = request.POST.get('rule', '')
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
            room = GameRoom.objects.create(name=name, game_count=int(game_count), rule=rule, created_by=request.user)
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
    """対局ルーム削除（スーパーユーザ限定）"""
    room = get_object_or_404(GameRoom, pk=room_id)
    if request.method == 'POST':
        name = room.name
        room.delete()  # CASCADE で紐づくデータも全削除
        messages.success(request, f'対局ルーム「{name}」を削除しました。')
        return redirect('game_list')
    return render(request, 'accounts/game_delete.html', {'room': room})


# ──────────────── 対局結果一覧（機能9） ────────────────

@login_required
def game_result_list_view(request, room_id):
    room = get_object_or_404(GameRoom, pk=room_id)
    sessions = room.sessions.prefetch_related(
        'results', 'results__user', 'rounds', 'rounds__player_results'
    ).all()
    members = [m.user for m in room.members.select_related('user')]

    summary = []
    for user in members:
        results = GameResult.objects.filter(session__room=room, user=user)
        total_pts = results.aggregate(s=Sum('pts'))['s'] or Decimal('0')
        avg_rank  = results.aggregate(a=Avg('rank'))['a']
        count     = results.count()
        details   = list(results.select_related('session').order_by('session__session_number'))
        summary.append({
            'user': user,
            'total_pts': total_pts,
            'avg_rank': round(avg_rank, 3) if avg_rank else None,
            'count': count,
            'details': details,
        })
    summary.sort(key=lambda x: x['total_pts'], reverse=True)

    return render(request, 'accounts/game_result_list.html', {
        'room': room,
        'sessions': sessions,
        'summary': summary,
        'members': members,
    })


# ──────────────── 対局結果登録（機能10） ────────────────

def _session_context(room, session, members, rounds, yakus, readonly, session_number, existing_rounds=None):
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
    readonly = session.is_finalized

    existing_rounds = []
    for rr in session.rounds.prefetch_related('player_results', 'yakus', 'yakus__yakus').order_by('round_number'):
        pr_map = {pr.seat: pr for pr in rr.player_results.all()}
        yaku_obj = rr.yakus.first()
        existing_rounds.append({
            'round_number': rr.round_number,
            'round_master_id': rr.round_master_id,
            'honba': rr.honba,
            'kyotaku': rr.kyotaku,
            'is_ryukyoku': rr.is_ryukyoku,
            'pr': {seat: {
                'is_agari':        pr_map[seat].is_agari        if seat in pr_map else False,
                'agari_method':    pr_map[seat].agari_method    if seat in pr_map else '',
                'is_houjuu':       pr_map[seat].is_houjuu       if seat in pr_map else False,
                'is_furo':         pr_map[seat].is_furo         if seat in pr_map else False,
                'furo_count':      pr_map[seat].furo_count      if seat in pr_map else 0,
                'is_riichi':       pr_map[seat].is_riichi       if seat in pr_map else False,
                'ryukyoku_state':  pr_map[seat].ryukyoku_state  if seat in pr_map else '',
                'kyoku_balance':   pr_map[seat].kyoku_balance   if seat in pr_map else 0,
                'kyotaku_points':  pr_map[seat].kyotaku_points  if seat in pr_map else 0,
                'points_after':    pr_map[seat].points_after    if seat in pr_map else 0,
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

    return render(request, 'accounts/session_form.html',
                  _session_context(room, session, members, rounds, yakus, readonly,
                                   session.session_number, existing_rounds))


def _save_session(request, room, session, session_number, members, rounds, yakus):
    finalize   = 'finalize' in request.POST
    game_date  = request.POST.get('game_date', '')
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
        # セッションがある場合は編集画面、ない場合は新規作成画面に戻す
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

        session.rounds.all().delete()

        round_count  = int(request.POST.get('round_count', 0))
        last_points  = {seat: 0 for seat in SEATS}

        for i in range(1, round_count + 1):
            rm_id = request.POST.get(f'round_{i}_master')
            if not rm_id:
                continue
            rm = get_object_or_404(RoundMaster, pk=rm_id)
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
            han  = int(request.POST.get(f'round_{i}_yaku_han', 0) or 0)
            dora = int(request.POST.get(f'round_{i}_yaku_dora', 0) or 0)
            ura  = int(request.POST.get(f'round_{i}_yaku_ura', 0) or 0)
            aka  = int(request.POST.get(f'round_{i}_yaku_aka', 0) or 0)
            yaku_ids = request.POST.getlist(f'round_{i}_yakus')

            ry = RoundYaku.objects.create(
                round_result=rr,
                agari_user_id=agari_uid  if agari_uid  else None,
                houjuu_user_id=houjuu_uid if houjuu_uid else None,
                han=han, dora=dora, ura_dora=ura, aka_dora=aka,
            )
            if yaku_ids:
                ry.yakus.set(YakuMaster.objects.filter(pk__in=yaku_ids))

        if finalize and round_count > 0:
            sorted_seats = sorted(SEATS, key=lambda s: last_points[s], reverse=True)
            GameResult.objects.filter(session=session).delete()
            for rank, seat in enumerate(sorted_seats, 1):
                pts = Decimal(last_points[seat]) / 1000 + RANK_BONUS[rank]
                setattr(session, f'rank_{seat}', rank)
                setattr(session, f'pts_{seat}',  pts)
                GameResult.objects.create(
                    session=session, user=seat_users[seat], seat=seat,
                    rank=rank, pts=pts, final_points=last_points[seat],
                )
            session.is_finalized = True

        session.save()

    messages.success(request, '対局を確定しました。' if finalize else '対局結果を保存しました。')
    return redirect('game_result_list', room_id=room.id)


# ──────────────── 対戦記録照会（機能11） ────────────────

@login_required
def battle_record_view(request):
    users = User.objects.all().order_by('username')
    rooms = GameRoom.objects.filter(
        Q(created_by=request.user) | Q(members__user=request.user)
    ).distinct()
    rules  = MahjongRule.objects.all()
    user_id  = request.GET.get('user_id', '')
    room_id  = request.GET.get('room_id', '')
    rule_id  = request.GET.get('rule_id', '')
    target_user = None
    stats = None

    if user_id:
        try:
            target_user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            pass

    if target_user:
        prr_qs = PlayerRoundResult.objects.filter(user=target_user)
        if room_id:
            prr_qs = prr_qs.filter(round_result__session__room_id=room_id)
        if rule_id:
            prr_qs = prr_qs.filter(round_result__session__room__rule_id=rule_id)

        prr_list     = list(prr_qs.select_related('round_result', 'round_result__session'))
        total_kyoku  = len(prr_list)
        agari_list   = [p for p in prr_list if p.is_agari]
        houjuu_list  = [p for p in prr_list if p.is_houjuu]
        tsumo_list   = [p for p in prr_list if p.is_agari and p.agari_method == 'tsumo']
        riichi_list  = [p for p in prr_list if p.is_riichi]
        furo_list    = [p for p in prr_list if p.is_furo]
        ryukyoku_list= [p for p in prr_list if p.round_result.is_ryukyoku]
        tenpai_list  = [p for p in prr_list if p.ryukyoku_state == 'tenpai']
        dama_list    = [p for p in agari_list if not p.is_riichi and not p.is_furo]
        riichi_agari   = [p for p in agari_list if p.is_riichi]
        riichi_houjuu  = [p for p in houjuu_list if p.is_riichi]
        riichi_ryukyoku= [p for p in ryukyoku_list if p.is_riichi]

        ippatsu_count = RoundYaku.objects.filter(
            round_result__in=[p.round_result for p in riichi_agari],
            agari_user=target_user, yakus__name='一発'
        ).count() if riichi_list else 0

        ura_agari = RoundYaku.objects.filter(
            round_result__in=[p.round_result for p in agari_list],
            agari_user=target_user, ura_dora__gte=1
        ).count() if agari_list else 0

        def safe_div(a, b): return round(a / b * 100, 2) if b else 0
        def safe_avg(lst, key): return round(sum(key(x) for x in lst) / len(lst)) if lst else 0

        agari_income = safe_avg(agari_list,  lambda p: p.kyoku_balance + p.kyotaku_points)
        houjuu_loss  = safe_avg(houjuu_list, lambda p: abs(p.kyoku_balance + p.kyotaku_points))
        riichi_balance = round(sum(p.kyoku_balance + p.kyotaku_points for p in riichi_list) / len(riichi_list)) if riichi_list else 0
        riichi_income  = safe_avg(riichi_agari,  lambda p: p.kyoku_balance + p.kyotaku_points)
        riichi_loss    = safe_avg(riichi_houjuu, lambda p: abs(p.kyoku_balance + p.kyotaku_points))
        kyoku_balance_avg = round(sum(p.kyoku_balance + p.kyotaku_points for p in prr_list) / total_kyoku) if total_kyoku else 0

        session_ids  = set(p.round_result.session_id for p in prr_list)
        battle_count = GameResult.objects.filter(session_id__in=session_ids, user=target_user).count()
        avg_rank_val = GameResult.objects.filter(session_id__in=session_ids, user=target_user).aggregate(a=Avg('rank'))['a']

        total_pts = None
        if room_id or rule_id:
            pts_qs = GameResult.objects.filter(user=target_user)
            if room_id: pts_qs = pts_qs.filter(session__room_id=room_id)
            if rule_id: pts_qs = pts_qs.filter(session__room__rule_id=rule_id)
            total_pts = pts_qs.aggregate(s=Sum('pts'))['s'] or Decimal('0')

        打点効率     = round(safe_div(len(agari_list), total_kyoku) / 100 * agari_income)
        銃点損失     = round(safe_div(len(houjuu_list), total_kyoku) / 100 * houjuu_loss)
        調整打点効率 = 打点効率 - 銃点損失

        stats = {
            'battle_count': battle_count, 'total_kyoku': total_kyoku,
            'agari_rate':    safe_div(len(agari_list), total_kyoku),
            'houjuu_rate':   safe_div(len(houjuu_list), total_kyoku),
            'tsumo_rate':    safe_div(len(tsumo_list), len(agari_list)),
            'dama_rate':     safe_div(len(dama_list), len(agari_list)),
            'ryukyoku_rate': safe_div(len(ryukyoku_list), total_kyoku),
            'tenpai_rate':   safe_div(len(tenpai_list), len(ryukyoku_list)),
            'furo_rate':     safe_div(len(furo_list), total_kyoku),
            'riichi_rate':   safe_div(len(riichi_list), total_kyoku),
            'avg_rank':      round(avg_rank_val, 3) if avg_rank_val else None,
            'agari_income':  agari_income, 'houjuu_loss': houjuu_loss,
            'riichi_agari_rate':    safe_div(len(riichi_agari), len(riichi_list)),
            'riichi_houjuu_rate':   safe_div(len(riichi_houjuu), len(riichi_list)),
            'riichi_balance':       riichi_balance,
            'riichi_income':        riichi_income,
            'riichi_loss':          riichi_loss,
            'riichi_ryukyoku_rate': safe_div(len(riichi_ryukyoku), len(riichi_list)),
            'ippatsu_rate':         safe_div(ippatsu_count, len(riichi_list)),
            'ura_rate':             safe_div(ura_agari, len(agari_list)),
            'daten_efficiency':     打点効率,
            'juten_loss':           銃点損失,
            'adjusted_efficiency':  調整打点効率,
            'kyoku_balance_avg':    kyoku_balance_avg,
            'total_pts':            total_pts,
        }

    return render(request, 'accounts/battle_record.html', {
        'users': users, 'rooms': rooms, 'rules': rules,
        'target_user': target_user, 'stats': stats,
        'sel_user_id': user_id, 'sel_room_id': room_id, 'sel_rule_id': rule_id,
    })
