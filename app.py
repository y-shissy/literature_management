import streamlit as st
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
import sqlite3
import os
import tempfile
import shutil

DB_FILE = "data.db"

# Google Drive 認証設定
def google_drive_auth(creds_file_path):
    gauth = GoogleAuth()
    gauth.LoadCredentialsFile(creds_file_path)
    if gauth.access_token_expired:
        gauth.Refresh()
    else:
        gauth.Authorize()
    return GoogleDrive(gauth)

# SQLiteデータベースを初期化または接続
def initialize_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS pdf_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            link TEXT
        )
    """)
    conn.commit()
    conn.close()

# PDFファイルをGoogle Driveにアップロード
def upload_to_google_drive(drive, file):
    temp_file_path = f"/tmp/{file.name}"
    with open(temp_file_path, "wb") as temp_file:
        temp_file.write(file.read())

    gfile = drive.CreateFile({"title": file.name})
    gfile.SetContentFile(temp_file_path)
    try:
        gfile.Upload()
        st.success(f"{file.name} をGoogle Driveにアップロードしました。")
    except Exception as e:
        st.error(f"アップロード失敗: {e}")
        return None

    # アクセス権を変更：リンクを持つ誰でも閲覧可能にする
    try:
        gfile.InsertPermission({
            'type': 'anyone',
            'role': 'reader',
        })
        st.success(f"{file.name} のアクセス権を変更しました。")
    except Exception as e:
        st.error(f"権限設定に失敗しました: {e}")

    os.remove(temp_file_path)
    return f"https://drive.google.com/uc?id={gfile['id']}"

# Google DriveからSQLiteデータベースをダウンロード
def download_db_from_google_drive(drive):
    file_list = drive.ListFile({'q': f"title='{DB_FILE}' and trashed=false"}).GetList()
    if file_list:
        gfile = file_list[0]  # 最初のファイルを取得
        gfile.GetContentFile(DB_FILE)  # ローカルにデータベースを保存
        st.success(f"{DB_FILE} をGoogle Driveからダウンロードしました。")
    else:
        st.error(f"{DB_FILE} がGoogle Drive内に見つかりません。新規作成します。")
        initialize_db()

# Google DriveにSQLiteデータベースをアップロード
def upload_db_to_google_drive(drive):
    # Google Drive上のファイルを検索
    file_list = drive.ListFile({'q': f"title='{DB_FILE}' and trashed=false"}).GetList()
    if file_list:
        gfile = file_list[0]  # 最初のファイルを取得
    else:
        gfile = drive.CreateFile({"title": DB_FILE})

    temp_db_path = f"/tmp/{DB_FILE}"
    shutil.move(DB_FILE, temp_db_path)  # 一時ファイルに移動

    gfile.SetContentFile(temp_db_path)
    try:
        gfile.Upload()
        st.success(f"{DB_FILE} をGoogle Driveにアップロードしました。")
    except Exception as e:
        st.error(f"データベースアップロードに失敗しました: {e}")
        return None

    # アクセス権を変更：リンクを持つ誰でも閲覧可能にする
    try:
        gfile.InsertPermission({
            'type': 'anyone',
            'role': 'reader',
        })
        st.success(f"{DB_FILE} のアクセス権を変更しました。")
    except Exception as e:
        st.error(f"権限設定に失敗しました: {e}")

    os.remove(temp_db_path)  # 一時ファイルを削除

    return f"https://drive.google.com/uc?id={gfile['id']}"

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

# Google Driveからデータベースをダウンロード
download_db_from_google_drive(drive)

# データベースの初期化
initialize_db()

# アップロードされたPDFを処理
uploaded_file = st.file_uploader("PDFをアップロード", type=["pdf"])
if uploaded_file:
    # Google Drive にPDFをアップロード
    file_link = upload_to_google_drive(drive, uploaded_file)

    if file_link:  # アップロードに成功した場合
        # SQLiteデータベースに記録
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        try:
            c.execute("INSERT INTO pdf_data (title, link) VALUES (?, ?)", (uploaded_file.name, file_link))
            conn.commit()
            st.success("PDFの情報がデータベースに追加されました！")
        except Exception as e:
            st.error(f"データベースへの追加に失敗しました: {e}")
        finally:
            conn.close()

        # データベースをGoogle Driveにアップロード
        db_link = upload_db_to_google_drive(drive)
        if db_link:  # データベースアップロードに成功した場合
            st.success("データベースを更新しました！")
            st.write(f"PDFリンク: [ここをクリック]({file_link})")
            st.write(f"データベースはGoogle Driveにアップロードされました。リンク: [ここをクリック]({db_link})")

# データベースから保存済みPDFを表示
try:
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