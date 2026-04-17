from django import template
register = template.Library()

@register.filter
def get_player(session, seat):
    return getattr(session, f'player_{seat}', '')

@register.filter
def get_player_id(session, seat):
    player = getattr(session, f'player_{seat}', None)
    return player.id if player else None

@register.filter
def get_pts(session, seat):
    return getattr(session, f'pts_{seat}', None)

@register.filter
def get_rank(session, seat):
    return getattr(session, f'rank_{seat}', None)
