import streamlit as st
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
from git import Repo
import sqlite3
import os
import tempfile
import json

# SQLiteデータベース名
DB_FILE = "data.db"

# Google Drive 認証設定
def google_drive_auth(creds_file_path):
    gauth = GoogleAuth()
    gauth.LoadCredentialsFile(creds_file_path)
    # 認証トークンが期限切れの場合は更新
    if gauth.access_token_expired:
        gauth.Refresh()
    else:
        gauth.Authorize()
    return GoogleDrive(gauth)

# Google Drive にPDFをアップロード
def upload_to_google_drive(drive, file):
    gfile = drive.CreateFile({"title": file.name})
    gfile.SetContentFile(file.name)
    gfile.Upload()
    return f"https://drive.google.com/uc?id={gfile['id']}"

# GitHubリポジトリからSQLiteデータベースを取得
def fetch_db_from_github():
    repo_url = f"https://{st.secrets.github.token}@github.com/{st.secrets.github.repo}.git"
    local_dir = "temp_repo"
    if os.path.exists(local_dir):
        repo = Repo(local_dir)
        repo.remote().pull()
    else:
        repo = Repo.clone_from(repo_url, local_dir)
    db_path = os.path.join(local_dir, DB_FILE)
    if os.path.exists(db_path):
        os.rename(db_path, DB_FILE)

# SQLiteデータベースをGitHubリポジトリにプッシュ
def push_db_to_github():
    repo_url = f"https://{st.secrets.github.token}@github.com/{st.secrets.github.repo}.git"
    local_dir = "temp_repo"
    if not os.path.exists(local_dir):
        Repo.clone_from(repo_url, local_dir)
    repo = Repo(local_dir)
    db_path = os.path.join(local_dir, DB_FILE)
    os.rename(DB_FILE, db_path)
    repo.git.add(DB_FILE)
    repo.index.commit("Update database")
    repo.remote().push()

# Streamlitアプリの構成
st.title("PDF管理＆SQLiteデータベース管理アプリ")

# `mycreds.txt`ファイルをアップロード
uploaded_creds_file = st.file_uploader("認証情報ファイル (`mycreds.txt`) をアップロード", type=["txt"])
if not uploaded_creds_file:
    st.warning("Google Driveの認証には`mycreds.txt`ファイルをアップロードしてください。")
    st.stop()

# 一時ファイルとして`mycreds.txt`を保存
with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as temp_creds_file:
    temp_creds_file.write(uploaded_creds_file.read())
    temp_creds_path = temp_creds_file.name

# Google Drive 認証
try:
    drive = google_drive_auth(temp_creds_path)
except Exception as e:
    st.error(f"Google Drive認証に失敗しました: {e}")
    st.stop()

# アップロードされたPDFを処理
uploaded_file = st.file_uploader("PDFをアップロード", type=["pdf"])
if uploaded_file:
    # Google Drive にアップロード
    file_link = upload_to_google_drive(drive, uploaded_file)

    # SQLiteデータベースに記録
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS pdf_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            link TEXT
        )
    """)
    c.execute("INSERT INTO pdf_data (title, link) VALUES (?, ?)", (uploaded_file.name, file_link))
    conn.commit()
    conn.close()

    # データベースをGitHubにプッシュ
    try:
        push_db_to_github()
        st.success("PDFをアップロードし、データベースを更新しました！")
        st.write(f"リンク: [ここをクリック]({file_link})")
    except Exception as e:
        st.error(f"データベースのGitHub同期に失敗しました: {e}")

# データベースから保存済みPDFを表示
try:
    fetch_db_from_github()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT title, link FROM pdf_data")
    rows = c.fetchall()
    st.write("保存済みPDF一覧:")
    for row in rows:
        st.write(f"- {row[0]}: [リンク]({row[1]})")
    conn.close()
except Exception as e:
    st.error(f"データベースの取得に失敗しました: {e}")
