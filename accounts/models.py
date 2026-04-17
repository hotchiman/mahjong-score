from django.db import models
from django.contrib.auth.models import User
from django.db.models import Avg, Sum, Count, Q
import uuid


# ─────────────────────────────────────────────
#  既存モデル
# ─────────────────────────────────────────────

class MahjongRule(models.Model):
    """対局ルールマスタ"""
    name = models.CharField('ルール名', max_length=100)
    detail = models.TextField('ルール詳細')
    order = models.PositiveIntegerField('表示順', default=0)

    class Meta:
        ordering = ['order']
        verbose_name = '対局ルール'
        verbose_name_plural = '対局ルール'

    def __str__(self):
        return self.name


class GameRoom(models.Model):
    """対局ルーム"""
    name = models.CharField('対局名', max_length=200)
    game_count = models.PositiveIntegerField('対局数')
    rule = models.ForeignKey(MahjongRule, on_delete=models.PROTECT, verbose_name='ルール')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_rooms', verbose_name='作成者')
    invite_token = models.UUIDField('招待トークン', default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField('作成日時', auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = '対局ルーム'
        verbose_name_plural = '対局ルーム'

    def __str__(self):
        return self.name


class GameRoomMember(models.Model):
    """対局ルームメンバー"""
    room = models.ForeignKey(GameRoom, on_delete=models.CASCADE, related_name='members', verbose_name='対局ルーム')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='joined_rooms', verbose_name='ユーザ')
    joined_at = models.DateTimeField('参加日時', auto_now_add=True)

    class Meta:
        unique_together = ('room', 'user')
        ordering = ['joined_at']
        verbose_name = 'メンバー'
        verbose_name_plural = 'メンバー'

    def __str__(self):
        return f'{self.room.name} - {self.user.username}'


# ─────────────────────────────────────────────
#  対局結果関連マスタ
# ─────────────────────────────────────────────

class RoundMaster(models.Model):
    """局マスタ（東1局〜西4局）"""
    name = models.CharField('局名', max_length=20)
    order = models.PositiveIntegerField('表示順', default=0)

    class Meta:
        ordering = ['order']
        verbose_name = '局マスタ'

    def __str__(self):
        return self.name


class YakuMaster(models.Model):
    """役マスタ"""
    name = models.CharField('役名', max_length=50)
    han = models.PositiveIntegerField('翻数', default=1)
    order = models.PositiveIntegerField('表示順', default=0)

    class Meta:
        ordering = ['order']
        verbose_name = '役マスタ'

    def __str__(self):
        return self.name


# ─────────────────────────────────────────────
#  対局セッション（1試合）
# ─────────────────────────────────────────────

SEAT_EAST  = 'east'
SEAT_SOUTH = 'south'
SEAT_WEST  = 'west'
SEAT_NORTH = 'north'
SEAT_CHOICES = [
    (SEAT_EAST,  '起家'),
    (SEAT_SOUTH, '南家'),
    (SEAT_WEST,  '西家'),
    (SEAT_NORTH, '北家'),
]

class GameSession(models.Model):
    """対局セッション（対局ルーム内の1試合）"""
    room = models.ForeignKey(GameRoom, on_delete=models.CASCADE, related_name='sessions', verbose_name='対局ルーム')
    session_number = models.PositiveIntegerField('対局No')
    game_date = models.DateField('対局日')

    # 座席ごとのユーザ
    player_east  = models.ForeignKey(User, on_delete=models.PROTECT, related_name='sessions_east',  verbose_name='起家')
    player_south = models.ForeignKey(User, on_delete=models.PROTECT, related_name='sessions_south', verbose_name='南家')
    player_west  = models.ForeignKey(User, on_delete=models.PROTECT, related_name='sessions_west',  verbose_name='西家')
    player_north = models.ForeignKey(User, on_delete=models.PROTECT, related_name='sessions_north', verbose_name='北家')

    # 対局確定フラグ
    is_finalized = models.BooleanField('確定済み', default=False)

    # 最終着順・獲得pts（確定時に計算）
    rank_east   = models.PositiveIntegerField('起家着順', null=True, blank=True)
    rank_south  = models.PositiveIntegerField('南家着順', null=True, blank=True)
    rank_west   = models.PositiveIntegerField('西家着順', null=True, blank=True)
    rank_north  = models.PositiveIntegerField('北家着順', null=True, blank=True)

    pts_east    = models.DecimalField('起家獲得pts', max_digits=8, decimal_places=1, null=True, blank=True)
    pts_south   = models.DecimalField('南家獲得pts', max_digits=8, decimal_places=1, null=True, blank=True)
    pts_west    = models.DecimalField('西家獲得pts', max_digits=8, decimal_places=1, null=True, blank=True)
    pts_north   = models.DecimalField('北家獲得pts', max_digits=8, decimal_places=1, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['session_number']
        unique_together = ('room', 'session_number')
        verbose_name = '対局セッション'

    def __str__(self):
        return f'{self.room.name} 対局{self.session_number}'

    def get_player(self, seat):
        return getattr(self, f'player_{seat}')

    def get_rank(self, seat):
        return getattr(self, f'rank_{seat}')

    def get_pts(self, seat):
        return getattr(self, f'pts_{seat}')


# ─────────────────────────────────────────────
#  局結果（1局ごと）
# ─────────────────────────────────────────────

AGARI_METHOD_TSUMO = 'tsumo'
AGARI_METHOD_RON   = 'ron'
AGARI_METHOD_CHOICES = [
    (AGARI_METHOD_TSUMO, 'ツモ'),
    (AGARI_METHOD_RON,   'ロン'),
]

RYUKYOKU_STATE_TENPAI   = 'tenpai'
RYUKYOKU_STATE_NOTEN    = 'noten'
RYUKYOKU_STATE_CHOICES = [
    (RYUKYOKU_STATE_TENPAI, '聴牌'),
    (RYUKYOKU_STATE_NOTEN,  '不聴'),
    ('', '—'),
]

class RoundResult(models.Model):
    """局結果"""
    session = models.ForeignKey(GameSession, on_delete=models.CASCADE, related_name='rounds', verbose_name='対局セッション')
    round_number = models.PositiveIntegerField('局No')
    round_master = models.ForeignKey(RoundMaster, on_delete=models.PROTECT, verbose_name='局')
    honba = models.PositiveIntegerField('本場', default=0)
    kyotaku = models.PositiveIntegerField('供託本数', default=0)
    is_ryukyoku = models.BooleanField('流局', default=False)

    class Meta:
        ordering = ['round_number']
        verbose_name = '局結果'

    def __str__(self):
        return f'{self.session} {self.round_master.name}{self.honba}本場'


class PlayerRoundResult(models.Model):
    """各プレイヤーの局結果"""
    round_result = models.ForeignKey(RoundResult, on_delete=models.CASCADE, related_name='player_results', verbose_name='局結果')
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='ユーザ')
    seat = models.CharField('座席', max_length=10, choices=SEAT_CHOICES)

    # 和了/放銃
    is_agari        = models.BooleanField('和了', default=False)
    agari_method    = models.CharField('和了方法', max_length=10, choices=AGARI_METHOD_CHOICES, blank=True, default='')
    is_houjuu       = models.BooleanField('放銃', default=False)

    # 副露・立直
    is_furo         = models.BooleanField('副露', default=False)
    furo_count      = models.PositiveIntegerField('副露回数', default=0)
    is_riichi       = models.BooleanField('立直', default=False)

    # 流局時
    ryukyoku_state  = models.CharField('流局時状態', max_length=10, choices=RYUKYOKU_STATE_CHOICES, blank=True, default='')

    # 点数
    kyoku_balance   = models.IntegerField('局収支', default=0)
    kyotaku_points  = models.IntegerField('供託点', default=0)
    points_after    = models.IntegerField('持ち点', default=0)

    class Meta:
        unique_together = ('round_result', 'user')
        verbose_name = 'プレイヤー局結果'

    def __str__(self):
        return f'{self.round_result} - {self.user.username}'


class RoundYaku(models.Model):
    """局の役記録"""
    round_result    = models.ForeignKey(RoundResult, on_delete=models.CASCADE, related_name='yakus', verbose_name='局結果')
    agari_user      = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='agari_yakus', verbose_name='和了者')
    houjuu_user     = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='houjuu_yakus', verbose_name='放銃者')
    han             = models.PositiveIntegerField('翻数', default=0)
    dora            = models.PositiveIntegerField('表ドラ', default=0)
    ura_dora        = models.PositiveIntegerField('裏ドラ', default=0)
    aka_dora        = models.PositiveIntegerField('赤ドラ', default=0)
    yakus           = models.ManyToManyField(YakuMaster, blank=True, verbose_name='役')

    class Meta:
        verbose_name = '役記録'

    def __str__(self):
        return f'{self.round_result} 役記録'


# ─────────────────────────────────────────────
#  集計用ヘルパー
# ─────────────────────────────────────────────

class GameResult(models.Model):
    """対局結果サマリ（確定時に保存）"""
    session = models.ForeignKey(GameSession, on_delete=models.CASCADE, related_name='results', verbose_name='対局セッション')
    user    = models.ForeignKey(User, on_delete=models.CASCADE, related_name='game_results', verbose_name='ユーザ')
    seat    = models.CharField('座席', max_length=10, choices=SEAT_CHOICES)
    rank    = models.PositiveIntegerField('着順')
    pts     = models.DecimalField('獲得pts', max_digits=8, decimal_places=1)
    final_points = models.IntegerField('最終持ち点')

    class Meta:
        unique_together = ('session', 'user')
        verbose_name = '対局結果サマリ'

    def pts_display(self):
        if self.pts < 0:
            return f'▲{abs(self.pts):.1f}'
        return f'+{self.pts:.1f}'
