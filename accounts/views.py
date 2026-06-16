from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, Avg, Sum, Count
from django.db import transaction
from decimal import Decimal, ROUND_HALF_UP
from collections import defaultdict
import json

from .models import (
    MahjongRule, GameRoom, GameRoomMember,
    GameSession, RoundResult, PlayerRoundResult, RoundYaku,
    RoundMaster, YakuMaster, GameResult,
)

SEATS = ['east', 'south', 'west', 'north']
SEAT_LABEL = {'east': '起家', 'south': '南家', 'west': '西家', 'north': '北家'}

# ── 対戦記録：役グループ定義 ──────────────────────────
YAKU_LUCK      = ['一発', '嶺上開花', '海底摸月', '河底撈魚', '天和', '地和']
YAKU_FLUSH     = ['混一色', '清一色', '緑一色', '字一色', '九蓮宝燈']
YAKU_MENZEN    = [
    '門前清自摸和', '立直', '一盃口', '平和', 'ダブル立直', '七対子', '二盃口', '一発',
    '天和', '地和', '四暗刻', '国士無双', '九蓮宝燈', '純正九蓮宝燈',
    '四暗刻単騎', '国士無双十三面待ち',
]
YAKU_TERMINAL  = ['混全帯么九', '混老頭', '純全帯么九', '字一色', '清老頭', '国士無双', '国士無双十三面待ち']
YAKU_TANYAO    = ['断么九']
YAKU_YAKUHAI   = [
    '役牌 白', '役牌 發', '役牌 中', '役牌 自風牌', '役牌 場風牌',
    '小三元', '大三元', '字一色', '小四喜', '大四喜',
]
YAKU_HAN1 = ['門前清自摸和', '立直', '搶槓', '嶺上開花', '海底摸月', '河底撈魚',
             '役牌 白', '役牌 發', '役牌 中', '役牌 自風牌', '役牌 場風牌',
             '断么九', '一盃口', '平和', '一発']
YAKU_HAN2 = ['混全帯么九', '一気通貫', '三色同順', 'ダブル立直', '三色同刻',
             '三槓子', '対々和', '三暗刻', '小三元', '混老頭', '七対子']
YAKU_HAN3 = ['純全帯么九', '混一色', '二盃口']
YAKU_HAN6 = ['清一色']
YAKU_YAKUMAN = ['天和', '地和', '大三元', '四暗刻', '字一色', '緑一色', '清老頭',
                '国士無双', '小四喜', '四槓子', '九蓮宝燈', '純正九蓮宝燈',
                '四暗刻単騎', '国士無双十三面待ち', '大四喜']


def _calc_point_rank_map(points, draw_handling):
    """
    持ち点(dict: seat->int) と同点の扱いから、各座席の着順(1〜4)を返す。
    JS版 calcRankMap と同じロジック。
    """
    sorted_seats = sorted(SEATS, key=lambda s: (-points[s], SEATS.index(s)))
    tmp = {s: i + 1 for i, s in enumerate(sorted_seats)}
    if draw_handling == 'east_priority':
        return tmp
    rank_map = {}
    done = set()
    for seat in SEATS:
        if seat in done:
            continue
        same = [s for s in SEATS if points[s] == points[seat]]
        top_rank = min(tmp[s] for s in same)
        for s in same:
            rank_map[s] = top_rank
            done.add(s)
    return rank_map


def _get_oya_seat(round_master_name):
    """局名（例：「東1」「南3」）の末尾数字から起家(東家)を基準とした親の座席を返す"""
    try:
        num = int(round_master_name[-1])
    except (ValueError, IndexError):
        return None
    return SEATS[(num - 1) % 4]


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
    draw_mode  = rule.draw_handling  # 'split_east' | 'split' | 'east_priority'

    pts_raw = {seat: D(last_points[seat]) for seat in SEATS}

    # ── 連盟Aルール（浮き人数連動ウマ）────────────────
    if rule.uma_type == 'rengo_a':
        return _calc_pts_rengo_a(pts_raw, return_pts, draw_mode)

    # ── 通常固定ウマ ─────────────────────────────────
    uma = [D(rule.uma1), D(rule.uma2), D(rule.uma3), D(rule.uma4)]

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
                # 端数は起家に最も近い1人に集約（3n+1 → +0.1、3n+2 → +0.2）
                unit = D('0.1')
                quotient = int(total_pts / unit) // count  # 小数第一位まで商
                remainder = int(total_pts / unit) - quotient * count  # あまり（0.1 pts 単位）
                # 座席順（起家優先）でグループを並べる
                same_sorted = sorted(same, key=lambda s: SEATS.index(s))
                for idx, s in enumerate(same_sorted):
                    extra = unit * remainder if idx == 0 else D('0')
                    result[s] = {'rank': top_rank, 'pts': D(quotient) * unit + extra}

            done.update(same)
    else:
        for rank, seat in enumerate(ranked, 1):
            result[seat] = {'rank': rank, 'pts': base_pts(seat, rank).quantize(D('0.1'), rounding=ROUND_HALF_UP)}

    return result


def _calc_pts_rengo_a(pts_raw, return_pts, draw_mode):
    """
    連盟Aルール専用のpts計算。
    - 浮き（最終持ち点 >= 返し点）の人数でウマテーブルを切り替え
    - 同点処理は draw_mode='split' に準拠
    """
    from .models import RENGO_A_UMA_TABLE
    D = Decimal

    # 浮き人数カウント（返し点以上 = 浮き）
    float_count = sum(1 for s in SEATS if pts_raw[s] >= return_pts)

    uma = [D(v) for v in RENGO_A_UMA_TABLE[float_count]]

    # 起家優先で仮着順を決める
    def seat_priority(seat): return SEATS.index(seat)
    ranked = sorted(SEATS, key=lambda s: (-pts_raw[s], seat_priority(s)))

    def base_pts(seat, rank):
        # 連盟Aルール: 返し点 = 初期点なので1着ボーナスは発生しない
        return (pts_raw[seat] - return_pts) / 1000 + uma[rank - 1]

    # 仮pts計算
    tmp = {}
    for rank, seat in enumerate(ranked, 1):
        tmp[seat] = {'rank': rank, 'pts': base_pts(seat, rank)}

    # 同点グループを split（均等分け）で処理
    result = {}
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

        top_rank  = min(tmp[s]['rank'] for s in same)
        total_pts = sum(tmp[s]['pts'] for s in same)
        count     = len(same)
        avg = (total_pts / count).quantize(D('0.1'), rounding=ROUND_HALF_UP)
        for s in same:
            result[s] = {'rank': top_rank, 'pts': avg}
        done.update(same)

    return result


def apply_zankyo(calc, zankyo, kyotaku_handling, seat_users):
    """
    残供託を calc（calc_pts_with_rule の戻り値）に加算して返す。
    ※順位・順位ウマは変動しない。ptsのみ加算。

    zankyo          : 残供託本数（int）
    kyotaku_handling: 'top_split' | 'top_east' | 'carryover'
    seat_users      : {seat: User}

    calc の構造: { seat: {'rank': int, 'pts': Decimal} }
    """
    D = Decimal

    if kyotaku_handling == 'carryover' or zankyo == 0:
        return calc

    # 1着の座席を取得（同着あり）
    min_rank = min(v['rank'] for v in calc.values())
    top_seats = [s for s in SEATS if calc[s]['rank'] == min_rank]

    result = {s: {'rank': calc[s]['rank'], 'pts': calc[s]['pts']} for s in SEATS}

    if kyotaku_handling == 'top_east':
        # 起家優先：複数1着でも起家に最も近い1人が総取り
        winner = min(top_seats, key=lambda s: SEATS.index(s))
        result[winner]['pts'] += D(zankyo)  # zankyo本 × 1000点 / 1000 = zankyo pts
        return result

    # 'top_split': 1着複数の場合は人数で分ける（端数は起家優先）
    count = len(top_seats)

    if count == 1:
        result[top_seats[0]]['pts'] += D(zankyo)
        return result

    if count == 2:
        total = D(zankyo)
        half  = (total / 2).quantize(D('0.1'), rounding=ROUND_HALF_UP)
        for s in top_seats:
            result[s]['pts'] += half
        return result

    if count == 4:
        total   = D(zankyo)
        quarter = (total / 4).quantize(D('0.1'), rounding=ROUND_HALF_UP)
        for s in top_seats:
            result[s]['pts'] += quarter
        return result

    # count == 3
    # 商（3n本）は3等分、端数（0〜2本）は起家優先で「400/300点」配分
    quotient  = zankyo // 3          # 3n
    remainder = zankyo % 3           # 0, 1, 2

    base_pts = D(quotient)           # quotient本 × 1000点 / 1000

    # 起家優先でソート
    top_sorted = sorted(top_seats, key=lambda s: SEATS.index(s))

    if remainder == 0:
        for s in top_sorted:
            result[s]['pts'] += base_pts
    else:
        # 端数処理：起家に近い1人が +0.4pts × remainder、残り2人が +0.3pts × remainder
        # （400点 / 1000 = 0.4pts、300点 / 1000 = 0.3pts）
        east_extra  = D('0.4') * remainder
        other_extra = D('0.3') * remainder
        for idx, s in enumerate(top_sorted):
            extra = east_extra if idx == 0 else other_extra
            result[s]['pts'] += base_pts + extra

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

    # 現セッション以前の確定済みセッションの累計pts（ユーザID→累計pts）
    # 編集中のセッション自身は除く
    finalized_sessions = room.sessions.filter(is_finalized=True)
    if session and session.pk:
        finalized_sessions = finalized_sessions.exclude(pk=session.pk)

    cum_pts = {}  # user_id -> float
    for sess in finalized_sessions:
        for seat in ('east', 'south', 'west', 'north'):
            player = getattr(sess, f'player_{seat}')
            pts    = getattr(sess, f'pts_{seat}')
            if player and pts is not None:
                cum_pts[player.id] = float(cum_pts.get(player.id, 0)) + float(pts)

    # メンバーの累計pts（未参加メンバーは0）
    cum_pts_json = json.dumps({str(m.id): cum_pts.get(m.id, 0.0) for m in members})

    # ルール情報をJSに渡す（累計pts計算用）
    from .models import RENGO_A_UMA_TABLE
    rule_json = json.dumps({
        'init_points':       rule.init_points,
        'return_points':     rule.return_points,
        'draw_handling':     rule.draw_handling,
        'kyotaku_handling':  rule.kyotaku_handling,
        'uma_type':          rule.uma_type,
        'uma1': rule.uma1, 'uma2': rule.uma2,
        'uma3': rule.uma3, 'uma4': rule.uma4,
        'rengo_a_uma_table': RENGO_A_UMA_TABLE,
    })

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
        'return_points': rule.return_points,
        'draw_handling': rule.draw_handling,
        'kyotaku_handling': rule.kyotaku_handling,
        'cum_pts_json': cum_pts_json,
        'rule_json': rule_json,
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

        # オーラス情報（残供託計算用）
        last_round_kyotaku    = 0   # オーラス開始時の供託数
        last_round_ryukyoku   = False
        last_round_riichi_cnt = 0   # オーラスの立直者数
        last_round_idx        = 0   # 実際に保存された最終局番号

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

            # この局の立直者数（riichi フラグが立っている座席を数える）
            riichi_cnt = sum(
                1 for seat in SEATS
                if request.POST.get(f'round_{i}_{seat}_riichi') == '1'
            )

            # オーラス情報を更新（最後に通過した局が確定値になる）
            last_round_kyotaku    = kyotaku
            last_round_ryukyoku   = is_ryukyoku
            last_round_riichi_cnt = riichi_cnt
            last_round_idx        = i

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

            # ── 残供託の算出 ─────────────────────────────
            # オーラスが流局でない → 残供託0本
            # オーラスが流局 → 開始時供託数 + オーラス立直者数
            if last_round_ryukyoku:
                zankyo = last_round_kyotaku + last_round_riichi_cnt
            else:
                zankyo = 0

            calc = calc_pts_with_rule(last_points, seat_users, rule)

            # ── 残供託の配分 ─────────────────────────────
            if zankyo > 0:
                calc = apply_zankyo(calc, zankyo, rule.kyotaku_handling, seat_users)

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

    prr_list      = list(prr_qs.select_related('round_result', 'round_result__session', 'round_result__round_master'))
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
    second_rate  = safe_div(rank_dist[2], battle_count)
    third_rate   = safe_div(rank_dist[3], battle_count)
    forth_rate   = safe_div(rank_dist[4], battle_count)
    avoid4_rate  = safe_div(rank_dist[1] + rank_dist[2] + rank_dist[3], battle_count)
    sanka_rate   = round(safe_div(len(riichi_list), total_kyoku) + safe_div(len(furo_list), total_kyoku), 2)

    # 副露和了率、副露放銃率、平均副露回数、平均ドラ枚数、平均裏ドラ枚数、放銃時裏ドラ率、放銃時平均ドラ枚数、放銃時平均裏ドラ枚数
    def safe_avg2(lst, key): return round(sum(key(x) for x in lst) / len(lst), 2) if lst else 0

    furo_agari_list     = [p for p in prr_list if p.is_agari and p.is_furo]
    furo_houjuu_list    = [p for p in prr_list if p.is_houjuu and p.is_furo]
    furo_avg            = round(sum(p.furo_count for p in prr_list) / total_kyoku, 2) if total_kyoku else 0

    agari_dora_list = list(RoundYaku.objects.filter(
        round_result__in=[p.round_result for p in agari_list],
        agari_user=target_user
    ).prefetch_related('yakus'))
    all_dora_avg        = safe_avg2(agari_dora_list,  lambda p: p.dora + p.ura_dora + p.aka_dora)
    ura_dora_avg        = round(sum(p.ura_dora for p in agari_dora_list) / len(riichi_agari), 2) if len(riichi_agari) else 0

    ura_houju = RoundYaku.objects.filter(
        round_result__in=[p.round_result for p in houjuu_list],
        houjuu_user=target_user, ura_dora__gte=1
    ).count() if houjuu_list else 0
    houjuu_dora_list = list(RoundYaku.objects.filter(
        round_result__in=[p.round_result for p in houjuu_list],
        houjuu_user=target_user
    ))
    houjuu_ura_dora_list = RoundYaku.objects.filter(
        round_result__in=[p.round_result for p in houjuu_list],
        houjuu_user=target_user, yakus__name='立直'
    )
    houjuu_all_dora_avg = safe_avg2(houjuu_dora_list,  lambda p: p.dora + p.ura_dora + p.aka_dora)
    houjuu_ura_dora_avg = round(sum(p.ura_dora for p in houjuu_dora_list) / len(houjuu_ura_dora_list), 2) if len(houjuu_ura_dora_list) else 0

    # ──────────────────────────────────────────
    # 追加統計
    # ──────────────────────────────────────────
    total_agari = len(agari_list)

    # トップラス麻雀率／モブ率
    top_last_rate = safe_div(rank_dist[1] + rank_dist[4], battle_count)
    mob_rate      = safe_div(rank_dist[2] + rank_dist[3], battle_count)

    # 関与率
    kanyo_rate = round(safe_div(len(agari_list), total_kyoku) + safe_div(len(houjuu_list), total_kyoku), 2)

    # 傍観率：局収支・供託点ともに0の局
    boukan_count = sum(1 for p in prr_list if p.kyoku_balance == 0 and p.kyotaku_points == 0)
    boukan_rate  = safe_div(boukan_count, total_kyoku)

    # 供託点合計
    kyotaku_total = sum(p.kyotaku_points for p in prr_list)

    # 和了回数・放銃回数
    agari_count  = total_agari
    houjuu_count = len(houjuu_list)

    # 翻数・ドラ集計（和了時）
    han_total         = sum(p.han for p in agari_dora_list)
    dora_omote_total  = sum(p.dora     for p in agari_dora_list)
    dora_ura_total    = sum(p.ura_dora for p in agari_dora_list)
    dora_aka_total    = sum(p.aka_dora for p in agari_dora_list)
    dora_total        = dora_omote_total + dora_ura_total + dora_aka_total
    avg_han           = safe_avg2(agari_dora_list, lambda p: p.han)
    dora_ratio        = safe_div(dora_total, han_total)

    # 翻数・ドラ集計（放銃時）
    houjuu_han_total        = sum(p.han for p in houjuu_dora_list)
    houjuu_dora_omote_total = sum(p.dora     for p in houjuu_dora_list)
    houjuu_dora_ura_total   = sum(p.ura_dora for p in houjuu_dora_list)
    houjuu_dora_aka_total   = sum(p.aka_dora for p in houjuu_dora_list)
    houjuu_dora_total       = houjuu_dora_omote_total + houjuu_dora_ura_total + houjuu_dora_aka_total
    houjuu_avg_han          = safe_avg2(houjuu_dora_list, lambda p: p.han)
    houjuu_dora_ratio       = safe_div(houjuu_dora_total, houjuu_han_total)

    # 裏3回数・裏3放銃回数
    ura3_count        = sum(1 for p in agari_dora_list if p.ura_dora >= 3)
    ura3_houjuu_count = sum(1 for p in houjuu_dora_list if p.ura_dora >= 3)

    # 役一覧（M2M）を事前展開
    agari_yaku_sets = [set(y.name for y in r.yakus.all()) for r in agari_dora_list]

    def group_count(group):
        g = set(group)
        return sum(1 for names in agari_yaku_sets if names & g)

    luck_yaku_rate     = safe_div(group_count(YAKU_LUCK),     total_agari)
    flush_yaku_rate    = safe_div(group_count(YAKU_FLUSH),    total_agari)
    menzen_yaku_rate   = safe_div(group_count(YAKU_MENZEN),   total_agari)
    terminal_yaku_rate = safe_div(group_count(YAKU_TERMINAL), total_agari)
    tanyao_rate        = safe_div(group_count(YAKU_TANYAO),   total_agari)
    yakuhai_rate       = safe_div(group_count(YAKU_YAKUHAI),  total_agari)
    han1_yaku_rate     = safe_div(group_count(YAKU_HAN1),     total_agari)
    han2_yaku_rate     = safe_div(group_count(YAKU_HAN2),     total_agari)
    han3_yaku_rate     = safe_div(group_count(YAKU_HAN3),     total_agari)
    han6_yaku_rate     = safe_div(group_count(YAKU_HAN6),     total_agari)
    yakuman_yaku_rate  = safe_div(group_count(YAKU_YAKUMAN),  total_agari)

    # 役複合率（ドラは含まない）
    fukugou_count = sum(1 for names in agari_yaku_sets if len(names) >= 2)
    fukugou_rate  = safe_div(fukugou_count, total_agari)

    # 翻数別和了率（RoundYaku.han ベース）
    han1_rate = safe_div(sum(1 for p in agari_dora_list if p.han == 1), total_agari)
    han2_rate = safe_div(sum(1 for p in agari_dora_list if p.han == 2), total_agari)
    han3_rate = safe_div(sum(1 for p in agari_dora_list if p.han == 3), total_agari)
    han4_rate = safe_div(sum(1 for p in agari_dora_list if p.han == 4), total_agari)
    han5_rate = safe_div(sum(1 for p in agari_dora_list if p.han == 5), total_agari)
    han11plus_rate = safe_div(sum(1 for p in agari_dora_list if p.han >= 11), total_agari)
    haneman_rate = safe_div(sum(1 for p in agari_dora_list if p.han in (6, 7)), total_agari)
    baiman_rate  = safe_div(sum(1 for p in agari_dora_list if p.han in (8, 9, 10)), total_agari)
    sanbaiman_rate = round(han11plus_rate - yakuman_yaku_rate, 2)
    yakuman_rate   = yakuman_yaku_rate

    # 親判定（局名末尾の数字から）
    def is_oya(p):
        return _get_oya_seat(p.round_result.round_master.name) == p.seat

    mangan_count = sum(
        1 for p in agari_list
        if (is_oya(p) and p.kyoku_balance == 12000) or (not is_oya(p) and p.kyoku_balance == 8000)
    )
    mangan_rate = safe_div(mangan_count, total_agari)

    kobayashi_count = sum(
        1 for p in agari_list
        if (is_oya(p) and p.kyoku_balance == 1500)
        or (not is_oya(p) and p.kyoku_balance in (1000, 1100))
    )
    kobayashi_rate = safe_div(kobayashi_count, total_agari)

    kurosawa_rate = round(mangan_rate + haneman_rate + baiman_rate + sanbaiman_rate + yakuman_rate, 2)

    # その他キャラ役率
    haggy_rate  = safe_div(group_count(['三色同順']), total_agari)
    saki_rate   = safe_div(group_count(['嶺上開花']), total_agari)
    ama_e_rate  = safe_div(group_count(['海底摸月']), total_agari)

    # タコス率：開局（東1局0本場）の和了
    tacos_count = sum(
        1 for p in agari_list
        if p.round_result.round_master.name == '東1' and p.round_result.honba == 0
    )
    tacos_rate = safe_div(tacos_count, battle_count)

    # 亦野誠子率：副露数3での和了
    matano_count = sum(1 for p in agari_list if p.furo_count == 3)
    matano_rate  = safe_div(matano_count, total_agari)

    # 園城寺怜率：立直・一発・門前清自摸和が全て複合
    onjouji_set = {'立直', '一発', '門前清自摸和'}
    onjouji_count = sum(1 for names in agari_yaku_sets if onjouji_set <= names)
    onjouji_rate  = safe_div(onjouji_count, total_agari)

    # リーのみ率：役が「立直」のみ（ドラ有無は問わない）
    ri_nomi_count = sum(1 for names in agari_yaku_sets if names == {'立直'})
    ri_nomi_rate  = safe_div(ri_nomi_count, total_agari)

    # 鳴いたら降りるな率
    furo_tenpai_list = [p for p in prr_list if p.is_furo and p.ryukyoku_state == 'tenpai']
    naitara_numerator = len(furo_agari_list) + len(furo_houjuu_list) + len(furo_tenpai_list)
    naitara_rate = safe_div(naitara_numerator, len(furo_list))

    # 魂天力
    tamashii_power = rank_dist[1] * 70 + rank_dist[2] * 35 + rank_dist[3] * -5 + rank_dist[4] * -145

    # 原点超え率・オーラス和了率・オーラス着順UP率・マクラーレン率
    gr_list = list(gr_qs.select_related('session__room__rule'))
    genten_count = sum(1 for gr in gr_list if gr.final_points >= gr.session.room.rule.init_points)
    genten_chouka_rate = safe_div(genten_count, battle_count)

    prr_by_session = defaultdict(list)
    for p in prr_list:
        prr_by_session[p.round_result.session_id].append(p)

    # 全座席の各局の持ち点（オーラス開始時点の判定用）
    points_by_session_round = defaultdict(dict)
    for p in PlayerRoundResult.objects.filter(
        round_result__session_id__in=session_ids
    ).select_related('round_result'):
        key = (p.round_result.session_id, p.round_result.round_number)
        points_by_session_round[key][p.seat] = p.points_after

    gr_by_session = {gr.session_id: gr for gr in gr_list}

    oorasu_agari_count = 0
    rankup_count = 0
    maclaren_count = 0
    for sid, session_prrs in prr_by_session.items():
        if not session_prrs:
            continue
        max_round = max(p.round_result.round_number for p in session_prrs)
        last_prr = next((p for p in session_prrs if p.round_result.round_number == max_round), None)
        if last_prr and last_prr.is_agari:
            oorasu_agari_count += 1

        gr = gr_by_session.get(sid)
        if not gr or not last_prr:
            continue

        rule = gr.session.room.rule
        if max_round == 1:
            before_points = {s: rule.init_points for s in SEATS}
        else:
            before_points = points_by_session_round.get((sid, max_round - 1))
            if not before_points or len(before_points) != 4:
                continue

        before_rank_map = _calc_point_rank_map(before_points, rule.draw_handling)
        before_rank = before_rank_map.get(last_prr.seat)
        after_rank  = gr.rank
        if before_rank is not None:
            if after_rank < before_rank:
                rankup_count += 1
            elif after_rank > before_rank:
                maclaren_count += 1

    oorasu_agari_rate  = safe_div(oorasu_agari_count, battle_count)
    oorasu_rankup_rate = safe_div(rankup_count, battle_count)
    maclaren_rate      = safe_div(maclaren_count, battle_count)


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
        'ippatsu_rate':         safe_div(ippatsu_count, len(riichi_agari)),
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

        # ── 追加統計 ──
        'top_last_rate':        top_last_rate,
        'mob_rate':             mob_rate,
        'genten_chouka_rate':   genten_chouka_rate,
        'kanyo_rate':           kanyo_rate,
        'boukan_rate':          boukan_rate,
        'oorasu_agari_rate':    oorasu_agari_rate,
        'oorasu_rankup_rate':   oorasu_rankup_rate,
        'maclaren_rate':        maclaren_rate,
        'kyotaku_total':        kyotaku_total,
        'agari_count':          agari_count,
        'avg_han':              avg_han,
        'dora_total':           dora_total,
        'dora_omote_total':     dora_omote_total,
        'dora_ura_total':       dora_ura_total,
        'dora_aka_total':       dora_aka_total,
        'dora_ratio':           dora_ratio,
        'houjuu_count':         houjuu_count,
        'houjuu_avg_han':       houjuu_avg_han,
        'houjuu_dora_total':       houjuu_dora_total,
        'houjuu_dora_omote_total': houjuu_dora_omote_total,
        'houjuu_dora_ura_total':   houjuu_dora_ura_total,
        'houjuu_dora_aka_total':   houjuu_dora_aka_total,
        'houjuu_dora_ratio':       houjuu_dora_ratio,

        'luck_yaku_rate':     luck_yaku_rate,
        'flush_yaku_rate':    flush_yaku_rate,
        'menzen_yaku_rate':   menzen_yaku_rate,
        'terminal_yaku_rate': terminal_yaku_rate,
        'tanyao_rate':        tanyao_rate,
        'yakuhai_rate':       yakuhai_rate,
        'han1_yaku_rate':     han1_yaku_rate,
        'han2_yaku_rate':     han2_yaku_rate,
        'han3_yaku_rate':     han3_yaku_rate,
        'han6_yaku_rate':     han6_yaku_rate,
        'yakuman_yaku_rate':  yakuman_yaku_rate,

        'fukugou_rate':  fukugou_rate,
        'han1_rate':     han1_rate,
        'han2_rate':     han2_rate,
        'han3_rate':     han3_rate,
        'han4_rate':     han4_rate,
        'han5_rate':     han5_rate,
        'mangan_rate':   mangan_rate,
        'haneman_rate':  haneman_rate,
        'baiman_rate':   baiman_rate,
        'sanbaiman_rate':sanbaiman_rate,
        'yakuman_rate':  yakuman_rate,

        'kurosawa_rate':  kurosawa_rate,
        'kobayashi_rate': kobayashi_rate,

        'haggy_rate':  haggy_rate,
        'saki_rate':   saki_rate,
        'tacos_rate':  tacos_rate,
        'ama_e_rate':  ama_e_rate,
        'matano_rate': matano_rate,
        'onjouji_rate':onjouji_rate,

        'naitara_rate':      naitara_rate,
        'ri_nomi_rate':      ri_nomi_rate,
        'ura3_count':        ura3_count,
        'ura3_houjuu_count': ura3_houjuu_count,
        'tamashii_power':    tamashii_power,
        
        'second_rate':    second_rate,
        'third_rate':     third_rate,
        'forth_rate':     forth_rate,
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

            # ── 最良/最悪の色付け ─────────────────────────
            # 降順（大きい方が良い）
            DESC_KEYS = [
                'avg_score', 'agari_rate', 'riichi_rate', 'furo_rate', 'sanka_rate',
                'agari_income', 'top_rate', 'avoid4_rate', 'tsumo_rate', 'dama_rate',
                'ryukyoku_rate', 'tenpai_rate', 'riichi_agari_rate', 'riichi_balance',
                'riichi_income', 'ippatsu_rate', 'ura_rate', 'daten_efficiency',
                'adjusted_efficiency', 'kyoku_balance_avg',
                'furo_agari_rate', 'furo_avg', 'all_dora_avg', 'ura_dora_avg',
                'top_last_rate', 'mob_rate', 'genten_chouka_rate', 'kanyo_rate',
                'boukan_rate', 'oorasu_agari_rate', 'oorasu_rankup_rate',
                'kyotaku_total', 'agari_count', 'avg_han',
                'dora_total', 'dora_omote_total', 'dora_ura_total', 'dora_aka_total', 'dora_ratio',
                'luck_yaku_rate', 'flush_yaku_rate', 'menzen_yaku_rate', 'terminal_yaku_rate',
                'tanyao_rate', 'yakuhai_rate',
                'han1_yaku_rate', 'han2_yaku_rate', 'han3_yaku_rate', 'han6_yaku_rate',
                'yakuman_yaku_rate', 'fukugou_rate',
                'han1_rate', 'han2_rate', 'han3_rate', 'han4_rate', 'han5_rate',
                'mangan_rate', 'haneman_rate', 'baiman_rate', 'sanbaiman_rate',
                'yakuman_rate', 'kurosawa_rate', 'kobayashi_rate',
                'haggy_rate', 'saki_rate', 'tacos_rate', 'ama_e_rate',
                'matano_rate', 'onjouji_rate', 'naitara_rate', 'ri_nomi_rate',
                'ura3_count', 'tamashii_power','second_rate','third_rate','forth_rate',
            ]
            # 昇順（小さい方が良い）
            ASC_KEYS = [
                'houjuu_rate', 'houjuu_loss', 'avg_rank', 'riichi_houjuu_rate',
                'riichi_loss', 'riichi_ryukyoku_rate', 'juten_loss',
                'furo_houjuu_rate', 'houjuu_ura_rate', 'houjuu_all_dora_avg', 'houjuu_ura_dora_avg',
                'maclaren_rate', 'houjuu_count', 'houjuu_avg_han',
                'houjuu_dora_total', 'houjuu_dora_omote_total', 'houjuu_dora_ura_total',
                'houjuu_dora_aka_total', 'houjuu_dora_ratio', 'ura3_houjuu_count',
            ]

            def _val(row, key):
                v = row.get(key)
                try:
                    return float(v) if v is not None else None
                except (TypeError, ValueError):
                    return None

            for key in DESC_KEYS + ASC_KEYS:
                vals = [(_val(r, key), i) for i, r in enumerate(rows) if _val(r, key) is not None]
                if len(vals) < 2:
                    continue
                is_desc = key in DESC_KEYS
                sorted_vals = sorted(vals, key=lambda x: x[0], reverse=is_desc)
                best_val  = sorted_vals[0][0]
                worst_val = sorted_vals[-1][0]
                if best_val == worst_val:
                    continue
                for row in rows:
                    v = _val(row, key)
                    if v is None:
                        row[f'cls_{key}'] = ''
                    elif v == best_val:
                        row[f'cls_{key}'] = 'hi'
                    elif v == worst_val:
                        row[f'cls_{key}'] = 'lo'
                    else:
                        row[f'cls_{key}'] = ''

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
