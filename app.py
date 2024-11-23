import streamlit as st
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
from git import Repo
import sqlite3
import os
import json

# SQLiteデータベース名
DB_FILE = "data.db"

# Google Drive 認証設定
def google_drive_auth():
    # Streamlit Secretsから認証情報を読み込む
    secrets = st.secrets["gdrive"]
    client_config = {
        "web": {
            "client_id": secrets["client_id"],
            "project_id": secrets["project_id"],
            "auth_uri": secrets["auth_uri"],
            "token_uri": secrets["token_uri"],
            "auth_provider_x509_cert_url": secrets["auth_provider_x509_cert_url"],
            "client_secret": secrets["client_secret"],
            "redirect_uris": secrets["redirect_uris"],
        }
    }
    # `client_secrets.json`を一時的に作成
    with open("client_secrets.json", "w") as f:
        json.dump(client_config, f)

    # PyDriveの認証
    gauth = GoogleAuth()
    gauth.LoadClientConfigFile("client_secrets.json")
    gauth.LocalWebserverAuth()  # 認証フロー開始
    gauth.SaveCredentialsFile("mycreds.txt")  # 認証情報を保存
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

# Google Drive 認証
try:
    drive = google_drive_auth()
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
