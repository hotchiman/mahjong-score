from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('register/', views.register_view, name='register'),
    path('users/', views.user_list_view, name='user_list'),
    path('users/<int:user_id>/edit/', views.user_edit_view, name='user_edit'),
    path('users/<int:user_id>/delete/', views.user_delete_view, name='user_delete'),
    path('games/', views.game_list_view, name='game_list'),
    path('games/create/', views.game_create_view, name='game_create'),
    path('games/<int:room_id>/delete/', views.game_delete_view, name='game_delete'),
    path('games/join/<uuid:token>/', views.game_join_view, name='game_join'),
    path('games/<int:room_id>/results/', views.game_result_list_view, name='game_result_list'),
    path('games/<int:room_id>/sessions/new/', views.session_create_view, name='session_create'),
    path('games/<int:room_id>/sessions/<int:session_id>/', views.session_edit_view, name='session_edit'),
    path('records/', views.battle_record_view, name='battle_record'),
]
