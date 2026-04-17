# 🀄 麻雀対局管理システム (MJS)

## 機能一覧

| # | 画面 | URL | 説明 |
|---|------|-----|------|
| 1 | ログイン | `/login/` | ユーザ名・パスワードで認証 |
| 2 | ユーザ登録 | `/register/` | 新規ユーザの作成 |
| 3 | ユーザ照会 | `/users/` | 登録ユーザ一覧 |
| 4 | ユーザ編集 | `/users/<id>/edit/` | ユーザ情報の編集 |
| 5 | ユーザ削除 | `/users/<id>/delete/` | ユーザの削除 |
| 6 | 対局ルーム作成 | `/games/create/` | 対局ルームの作成（ルールマスタ選択） |
| 7 | 対局一覧 | `/games/` | 対局ルーム一覧・招待リンクコピー |
| 8 | 対局ルーム参加 | `/games/join/<token>/` | 招待リンクから参加 |

---

## セットアップ手順

```bash
# 1. 仮想環境の作成・有効化
python -m venv venv
source venv/bin/activate      # Mac/Linux
venv\Scripts\activate         # Windows

# 2. 依存パッケージのインストール
pip install -r requirements.txt

# 3. データベース初期化（ルールマスタも自動投入）
python manage.py migrate

# 4. 管理者ユーザ作成（任意）
python manage.py createsuperuser

# 5. 開発サーバ起動
python manage.py runserver
```

ブラウザで http://127.0.0.1:8000/ を開いてください。

---

## ルールマスタ

`migrate` 実行時に以下が自動登録されます：

| ルール名 | 詳細 |
|----------|------|
| ①Mルール | 25,000点持ち30,000返し、10-30 |
| ②雀魂ルール | 段位に応じる |

---

## ディレクトリ構成

```
django_project/
├── manage.py
├── requirements.txt
├── README.md
├── myproject/
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
└── accounts/
    ├── models.py           ← MahjongRule / GameRoom / GameRoomMember
    ├── views.py
    ├── urls.py
    ├── apps.py
    └── migrations/
        └── 0001_initial.py ← マスタデータ込みのマイグレーション
    └── templates/accounts/
        ├── base.html
        ├── login.html
        ├── register.html
        ├── user_list.html
        ├── user_edit.html
        ├── user_delete.html
        ├── game_list.html
        ├── game_create.html
        └── game_join.html
```
