#!/usr/bin/env bash
# Renderのビルド時に実行されるスクリプト
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate
