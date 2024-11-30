import streamlit as st
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
import sqlite3
import os
import tempfile
import shutil

from streamlit_pdf_viewer import pdf_viewer
import pandas as pd
import re
import fitz
import time
import requests
from langdetect import detect
from bs4 import BeautifulSoup
from sqlalchemy import Column, Integer, String, Boolean, create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from database import get_session, Metadata

from openai import OpenAI
from llama_index.core import download_loader, VectorStoreIndex, Settings, SimpleDirectoryReader,Document
import tiktoken
import urllib.parse

import pytesseract
from pdf2image import convert_from_path
import uuid
import hashlib

# PDFからの１~２ページのテキスト抽出（llama_index使用）
def extract_text_from_pdf_pages(pdf_path):
    #PDFファイル読込
    reader=SimpleDirectoryReader(input_files=[pdf_path])
    all_text=reader.load_data()
    return all_text[:2]

# OCR機能を使いPDFからテキスト抽出 (ページごと、日本語・英語対応)
def pdf_to_text_with_ocr_per_page_multi_lang(pdf_path):
    # PDFを画像に変換
    images = convert_from_path(pdf_path)
    page_texts = []

    # 画像ごとにOCRを適用
    for image in images:
        text = pytesseract.image_to_string(image, lang='jpn+eng')  # 日本語＋英語のOCR
        page_texts.append(text)

    return page_texts

# PDFからテキストを抽出し、Documentオブジェクトを生成
def extract_text_from_pdf(pdf_path):
    # SimpleDirectoryReaderで既存のドキュメントを読み込む
    reader = SimpleDirectoryReader(input_files=[pdf_path])
    documents = reader.load_data()

    ocr_cache = {}

    # documentsが空、またはすべてのテキストが空の場合はOCRを実行する
    perform_ocr = not documents or all(doc.text.strip() == "" for doc in documents)

    if perform_ocr:
        # OCR結果をキャッシュして効率化
        if pdf_path not in ocr_cache:
            ocr_cache[pdf_path] = pdf_to_text_with_ocr_per_page_multi_lang(pdf_path)

        # OCR結果をページごとに処理
        new_documents = []
        for page_number, text in enumerate(ocr_cache[pdf_path], start=1):
            metadata = {
                "page_label": str(page_number),  # ページ番号
                "file_name": os.path.basename(pdf_path),  # ファイル名
                "file_path": pdf_path  # フルパス
            }

            # LlamaIndexのDocumentオブジェクトを作成
            new_doc = Document(
                text=text,
                metadata=metadata,
            )
            new_documents.append(new_doc)

        # 新しいOCR結果をdocumentsに追加
        documents.extend(new_documents)

    return documents

#　抽出したテキストからDOI抽出
# DOIの正規表現パターン
doi_pattern = re.compile(r'(?i)\b(?:doi[:\s]*|DOI[:\s]*|https?://(?:dx\.doi\.org/|doi\.org/))?(10\.\d{4,9}/[-._;()/:A-Z0-9]+\b)')
def extract_doi(text):
    # 正規表現でDOIを抽出
    doi_matches = doi_pattern.findall(text)
    # 最初のDOIのみを返す
    return doi_matches[0] if doi_matches else None


# PDFからのテキスト抽出＋DOI抽出の関数（上記の組み合わせ）
def process_pdf(pdf_path):
    first_text=extract_text_from_pdf(pdf_path)[:2]
    combined_text = ' '.join([doc.text for doc in first_text]).replace('\n', ' ')  # 改行をスペースに置換
    first_doi = extract_doi(combined_text)
    return first_doi,first_text



# DOIから情報を抽出
def get_metadata_from_doi(doi):
    # Crossref APIを利用
    crossref_url = f"https://api.crossref.org/works/{doi}"
    try:
        response = requests.get(crossref_url, timeout=10,verify=False)  # タイムアウトを設定
        response.raise_for_status()  # ステータスコードが200以外の場合に例外を投げる
    except requests.RequestException as e:
        st.warning(f"Crossref API error: {e}")
        response = None

    if response and response.status_code == 200:
        try:
            data = response.json()
            metadata = data['message']
            return {
                'doi': doi,
                'タイトル': metadata.get('title', ['Not found'])[0],
                '著者': ', '.join(author['family'] + ' ' + author['given'] for author in metadata.get('author', [])),
                'ジャーナル': metadata.get('container-title', ['Not found'])[0],
                '巻': metadata.get('volume', 'Not found'),
                '号': metadata.get('issue', 'Not found'),
                '開始ページ': metadata.get('page', 'Not found').split('-')[0],
                '終了ページ': metadata.get('page', 'Not found').split('-')[-1],
                '年': metadata.get('published-print', {}).get('date-parts', [[None]])[0][0]
            }
        except (KeyError, ValueError, IndexError) as e:
            st.warning(f"Error parsing Crossref response: {e}")
    else:
        st.warning(f"Crossref API returned status code: {response.status_code if response else 'No response'}")
    
    # JALC REST APIを利用
    jalc_url = f"https://api.japanlinkcenter.org/dois/{doi}"
    try:
        response = requests.get(jalc_url, timeout=10,verify=False)
        response.raise_for_status()
    except requests.RequestException as e:
        st.warning(f"JALC API error: {e}")
        return None

    if response.status_code == 200:
        try:
            data = response.json()['data']
            
            # タイトルの取得 (日本語優先、なければ英語)
            title_info = next((title for title in data['title_list'] if title['lang'] == 'ja'), 
                              data['title_list'][0])
            title = title_info.get('title', 'Not found')

            # 著者名の取得 (日本語優先)
            authors_info = data.get('creator_list', [])
            authors = ', '.join(f"{name['last_name']} {name['first_name']}" 
                                for author in authors_info 
                                for name in author.get('names', []) 
                                if name.get('lang') == 'ja')

            # ジャーナル名の取得 (日本語優先、なければ英語)
            journal_info = next((journal for journal in data['journal_title_name_list'] if journal['lang'] == 'ja'), 
                                data['journal_title_name_list'][0])
            journal = journal_info.get('journal_title_name', 'Not found')

            # 発行年の取得
            year = data.get('publication_date', {}).get('publication_year', None)

            # ボリューム、ページの取得
            volume = data.get('volume', 'Not found')
            issue = data.get('issue', 'Not found')
            first_page = data.get('first_page', 'Not found')
            last_page = data.get('last_page', 'Not found')

            return {
                'doi': doi,
                'タイトル': title,
                '著者': authors,
                'ジャーナル': journal,
                '巻': volume,
                '号': issue,
                '開始ページ': first_page,
                '終了ページ': last_page,
                '年': year
            }
        except (KeyError, ValueError, IndexError) as e:
            st.warning(f"Error parsing JALC response: {e}")
    else:
        st.warning(f"JALC API returned status code: {response.status_code}")

    return None


# Google DriveにSQLiteデータベースをアップロード
def upload_db_to_google_drive(DB_FILE,drive):
    # Google Drive上のファイルを検索
    file_list = drive.ListFile({'q': f"title='{DB_FILE}' and trashed=false"}).GetList()
    if file_list:
        gfile = file_list[0]  # 最初のファイルを取得
    else:
        gfile = drive.CreateFile({"title": DB_FILE})

    temp_db_path = f"/tmp/{DB_FILE}"
    shutil.copy(DB_FILE, temp_db_path)  # 一時ファイルにコピー

    gfile.SetContentFile(temp_db_path)
    try:
        gfile.Upload()
        st.success(f"{DB_FILE} をGoogle Driveにアップロードしました。")
    except Exception as e:
        st.error(f"データベースアップロードに失敗しました: {e}")
        return None

    os.remove(temp_db_path)  # 一時ファイルを削除

    return f"https://drive.google.com/uc?id={gfile['id']}"

def sanitize_filename(filename):
    """ファイル名から不適切な文字を削除"""
    return re.sub(r'[\/:*?"<>|]', '', filename)

def store_metadata_in_db(DB_FILE, metadata, file_path, uploaded_file, drive):
    # セッションを作成
    DATABASE_URL = f"sqlite:///{DB_FILE}"
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    # カテゴリ，キーワード読み込み
    categories_all = st.session_state["categories_all"]
    keywords_all = st.session_state["keywords_all"]

    try:
        # DOIがすでに存在するか確認
        existing_record = session.query(Metadata).filter_by(doi=metadata['doi']).first()

        if existing_record:
            st.warning("This DOI is already in the database.")
            return

        # PDFファイル名をメタデータのタイトルに基づいて変更
        title = metadata.get("タイトル", "unnamed_document")  # タイトルがない場合のデフォルト値
        sanitized_title = sanitize_filename(title)
        new_filename = f"{sanitized_title}.pdf"

        # フォームの表示
        col1, col2 = st.columns([3, 1])

        with col1:
            st.markdown('#### アップロードしたPDF')
            # PDFの表示
            binary_data = uploaded_file.getvalue()
            pdf_viewer(input=binary_data, width=1000, height=1000)

        with col2:
            st.markdown('#### 入力フォーム')
            with st.form(key='metadata_form'):
                selected_category = st.selectbox('関連テーマ', categories_all)
                selected_keywords = st.multiselect('キーワード', keywords_all)
                memo = st.text_area('メモ')
                read = st.checkbox('既読の場合チェック', value=False)
                submit_button = st.form_submit_button(label='保存')

            if submit_button:
                # DOI URLを生成
                doi_url = f"https://doi.org/{metadata['doi']}"

                # 選択したキーワードをカンマ区切りの文字列に変換
                selected_keywords_str = ",".join(selected_keywords)

                # Google DriveにPDFをアップロード
                file_link = upload_to_google_drive(drive, file_path, new_filename)
                if not file_link:
                    st.error("Google Driveへのアップロードに失敗しました。")
                    return

                # 新しいメタデータレコードを作成
                new_record = Metadata(
                    doi=metadata['doi'],
                    タイトル=metadata["タイトル"],
                    著者=metadata["著者"],
                    ジャーナル=metadata["ジャーナル"],
                    巻=metadata["巻"],
                    号=metadata["号"],
                    開始ページ=metadata["開始ページ"],
                    終了ページ=metadata["終了ページ"],
                    年=metadata["年"],
                    doi_url=doi_url,
                    ファイルリンク=file_link,
                    キーワード=selected_keywords_str,
                    カテゴリ=selected_category,
                    メモ=memo,
                    Read=read
                )

                # データベースに追加
                session.add(new_record)
                session.commit()
                st.success("New record added to the database.")

                # データベースをGoogle Driveにアップロード
                upload_db_to_google_drive(DB_FILE, drive)

                return  # 成功した場合、処理をここで終了

    except Exception as e:
        st.warning(f"An error occurred: {e}")
        session.rollback()
    finally:
        session.close()


def store_metadata_in_db_ai(DB_FILE, metadata, file_path, uploaded_file, drive):
    # セッションを作成
    DATABASE_URL = f"sqlite:///{DB_FILE}"
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    # カテゴリ，キーワード読み込み
    categories_all = st.session_state["categories_all"]
    keywords_all = st.session_state["keywords_all"]

    try:
        # DOIがすでに存在するか確認
        existing_record = session.query(Metadata).filter_by(doi=metadata['doi']).first()

        if existing_record:
            st.warning("This DOI is already in the database.")
            return

        # PDFファイル名をメタデータのタイトルに基づいて変更
        title = metadata.get("タイトル", "unnamed_document")  # タイトルがない場合のデフォルト値
        sanitized_title = sanitize_filename(title)
        new_filename = f"{sanitized_title}.pdf"

        # PDFファイルからすべてのテキストを抽出
        content = extract_text_from_pdf(file_path)

        # 抽出したテキストから，要約とキーワードとカテゴリを取得
        summary, keyword_res, category_res = translate_and_summarize(content)
        st.write(summary)

        # キーワードを文字列に変換
        keywords_str = ','.join(keyword_res)

        # DOI URLを生成
        doi_url = f"https://doi.org/{metadata['doi']}"

        # Google DriveにPDFをアップロード
        file_link = upload_to_google_drive(drive, file_path, new_filename)
        if not file_link:
            st.error("Google Driveへのアップロードに失敗しました。")
            return

        # 新しいメタデータレコードを作成
        new_record = Metadata(
            doi=metadata['doi'],
            タイトル=metadata["タイトル"],
            著者=metadata["著者"],
            ジャーナル=metadata["ジャーナル"],
            巻=metadata["巻"],
            号=metadata["号"],
            開始ページ=metadata["開始ページ"],
            終了ページ=metadata["終了ページ"],
            年=metadata["年"],
            要約=summary,
            doi_url=doi_url,
            ファイルリンク=file_link,
            キーワード=keywords_str,
            カテゴリ=category_res,
            Read=False
        )

        # データベースに追加
        session.add(new_record)
        session.commit()
        st.success("New record added to the database.")

        # データベースをGoogle Driveにアップロード
        upload_db_to_google_drive(DB_FILE, drive)

        return  # 成功した場合、処理をここで終了

    except Exception as e:
        st.warning(f"An error occurred: {e}")
        session.rollback()
    finally:
        session.close()



## doiから情報を抽出する関数
def display_metadata(doi):
    metadata = get_metadata_from_doi(doi)
    if metadata:
        st.write(f"Title: {metadata["タイトル"]}")
        st.write(f"Authors: {metadata["著者"]}")
        st.write(f"Journal: {metadata["ジャーナル"]}")
        st.write(f"Volume: {metadata["巻"]}")
        st.write(f"Issue: {metadata["号"]}")
        st.write(f"First Page: {metadata["開始ページ"]}")
        st.write(f"Last Page: {metadata["終了ページ"]}")
        st.write(f"Year: {metadata["年"]}")
        return metadata
    else:
        st.warning("No data found for the provided DOI.")
        return None

## ファイル名を使ってdoiを抽出する関数
def search_doi_from_filename(filename):
    # まずCiNiiでDOIを検索
    doi = search_doi_on_cinii(filename)
    if doi:
        return doi
    else:
    #cross refでdoi検索
        doi = search_doi_on_crossref(filename)
        if doi:
            return doi
    return None

## Ciniiからdoiを抽出する関数
def search_doi_on_cinii(filename):
    # ファイル名のクリーンアップ（例: 拡張子や不要な部分を除去）
    name,ext=os.path.splitext(filename)
    # URLエンコーディング
    encoded_name=urllib.parse.quote(name)

    # CiNiiの検索URLにクリーンアップされたファイル名を使用
    search_url = f"https://cir.nii.ac.jp/opensearch/all?title={encoded_name}"

    try:
        # リクエストを送信してレスポンスを取得
        response = requests.get(search_url,verify=False)

        # レスポンスが成功したかを確認
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')

            # DOIリンクを検索
            for link in soup.find_all('a', href=True):
                if 'doi.org' in link['href']:
                    doi = link['href'].split("doi.org/")[-1]  # "doi.org/" 以降の部分を抽出
                    return doi
        else:
            st.error(f"Failed to retrieve data from CiNii. Status code: {response.status_code}")
    except requests.exceptions.RequestException as e:
        st.error(f"An error occurred during the request: {str(e)}")

    # DOIが見つからなかった場合はNoneを返す
    return None


## Ciniiからdoiを抽出する関数
def search_doi_on_crossref(filename):
    # ファイル名のクリーンアップ（例: 拡張子や不要な部分を除去）
    name,ext=os.path.splitext(filename)
    # URLエンコーディング
    encoded_name=urllib.parse.quote(name)

    # CiNiiの検索URLにクリーンアップされたファイル名を使用
    search_url = f"https://api.crossref.org/works?query.title={encoded_name}"

    try:
        # リクエストを送信してレスポンスを取得
        response = requests.get(search_url,verify=False)

        # レスポンスが成功したかを確認
        if response.status_code == 200:
            data=response.json()

            # DOIリンクを検索
            if 'items' in data['message']:
                for item in data["message"]["items"]:
                    if 'DOI' in item:
                        return item['DOI']
        else:
            st.error(f"Failed to retrieve data from CiNii. Status code: {response.status_code}")
    except requests.exceptions.RequestException as e:
        st.error(f"An error occurred during the request: {str(e)}")

    # DOIが見つからなかった場合はNoneを返す
    return None


# doiのリンク先を取得（リダイレクトをフォロー）
def get_final_url(doi_url):
    try:
        # リダイレクトをフォローして最終URLを取得
        response = requests.get(doi_url, allow_redirects=True,verify=False)
        response.raise_for_status()  # ステータスコードがエラーの場合は例外を発生させる
        
        # 最終的なリダイレクト先のURLを取得
        final_url = response.url
        
        return final_url
    except requests.RequestException as e:
        print(f"DOIリンクへのアクセスに失敗しました: {e}")
        return None
    
    
# doiのリンク先からアブストラクトを含む文字全文を抽出する関数
def get_abstract_from_url(url):
    try:
        response = requests.get(url,verify=False)
        response.raise_for_status()  # ステータスコードがエラーの場合は例外を発生させる
        soup = BeautifulSoup(response.content, "lxml")
        # ページの全文を取得（HTML全体のテキスト部分を取得する方法）
        full_text = soup.get_text(separator="\n", strip=True)
        return full_text
    except requests.RequestException as e:
        print(f"URLへのアクセスに失敗しました: {e}")
        return None
    

def translate_and_summarize(text):
    # カテゴリとキーワードをセッションから取得
    categories_all = st.session_state["categories_all"]
    keywords_all = st.session_state["keywords_all"]

    # OpenAI APIキーの設定とクライアント初期化
    openai_api_key = st.secrets["openai_api_key"]
    client = OpenAI(api_key=openai_api_key)

    # トークン制限設定
    model_name = "gpt-4o-mini"
    token_limit = 4000  # モデルの最大トークン数
    encoding = tiktoken.encoding_for_model(model_name)

    # テキストの前処理
    if not isinstance(text, str):
        text = str(text)

    text = re.sub(r'[\r\n\t]+', ' ', text)  # 改行・タブをスペースに置換
    text = re.sub(r'[^\x20-\x7E\u3000-\u9FFF]+', '', text)  # 特殊文字を除去

    # テキスト分割関数
    def split_text(text, max_tokens):
        tokens = encoding.encode(text)
        return [
            encoding.decode(tokens[i:i + max_tokens])
            for i in range(0, len(tokens), max_tokens)
        ]

    # テキスト分割処理
    max_text_tokens = token_limit - 1000
    if len(encoding.encode(text)) > max_text_tokens:
        text_chunks = split_text(text, max_text_tokens)
        multi_chunk = True
    else:
        text_chunks = [text]
        multi_chunk = False

    # 要約処理
    summaries = []
    try:
        for chunk in text_chunks:
            prompt = f"次の文章を日本語で簡潔に要約してください:\n\n{chunk}"
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}]
            )
            summaries.append(response.choices[0].message.content.strip())

        # 段階要約処理
        if multi_chunk:
            final_prompt = "以下の複数の要約をもとに、全体を通した簡潔な要約を作成してください:\n\n" + " ".join(summaries)
            final_response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": final_prompt}]
            )
            summary = final_response.choices[0].message.content.strip()
        else:
            summary = summaries[0]

    except Exception as e:
        st.error(f"要約中にエラーが発生しました: {e}")
        summary = "要約に失敗しました。"

    # キーワード抽出
    try:
        keyword_prompt = (
            f"次の要約に関連するキーワードを、以下のキーワードリストを参考にしてカンマ区切りで出力してください:\n"
            f"要約: {summary}\n\n"
            f"キーワードリスト: {', '.join(keywords_all)}"
        )
        keyword_response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": keyword_prompt}]
        )
        keyword_res = [kw.strip() for kw in keyword_response.choices[0].message.content.strip().split("、")]
    except Exception as e:
        st.error(f"キーワード抽出中にエラーが発生しました: {e}")
        keyword_res = []

    # カテゴリ選択
    try:
        category_prompt = (
            f"以下のカテゴリリストから、この要約に最も関連する語句を一つ選んで出力してください:\n"
            f"要約: {summary}\n\n"
            f"カテゴリ: {', '.join(categories_all)}"
        )
        category_response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": category_prompt}]
        )
        category_res = category_response.choices[0].message.content.strip()
    except Exception as e:
        st.error(f"カテゴリ選択中にエラーが発生しました: {e}")
        category_res = "カテゴリ選択に失敗しました。"

    return summary, keyword_res, category_res

def upload_to_google_drive(drive, file_path, filename):
    try:
        # 既存ファイルを検索
        existing_files = drive.ListFile({'q': f"title='{filename}' and trashed=false"}).GetList()

        if existing_files:
            gfile = existing_files[0]  # 最初のファイルを選択
            gfile.SetContentFile(file_path)  # 一時ファイルを新しい内容で設定
            gfile.Upload()
            st.success(f"既存のファイル '{filename}' をGoogle Driveに上書きしました。")
            file_link = f"https://drive.google.com/uc?id={gfile['id']}"
        else:
            gfile = drive.CreateFile({"title": filename})
            gfile.SetContentFile(file_path)
            gfile.Upload()
            st.success(f"{filename} をGoogle Driveにアップロードしました。")
            file_link = f"https://drive.google.com/uc?id={gfile['id']}"

        return file_link

    except Exception as e:
        st.error(f"アップロード失敗: {e}")
        return None

# PDFアップロード処理を共通化
def handle_pdf_upload(uploaded_file, auto_doi=False, manual_doi=None):
    try:
        # 一時ファイル作成
        temp_file_path, _ = create_temp_file(uploaded_file)
        if not temp_file_path:
            st.error("一時ファイルの作成に失敗しました。")
            return None, None

        # DOIの取得
        doi = None
        if auto_doi:
            doi, _ = process_pdf(temp_file_path)
            if not doi:
                search_term = os.path.splitext(uploaded_file.name)[0]
                doi = search_doi_from_filename(search_term)
        elif manual_doi:
            doi = manual_doi

        if not doi:
            st.error("DOIが見つかりませんでした。")
            return None, None

        # メタデータの取得
        metadata = display_metadata(doi)
        if not metadata:
            st.error("DOIに関連するメタデータが見つかりませんでした。")
            return None, None

        return metadata, temp_file_path

    except Exception as e:
        st.error(f"エラーが発生しました: {e}")
        return None, None


# 一時ファイルを作成する関数
def create_temp_file(uploaded_file):
    try:
        temp_file_path = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf").name
        with open(temp_file_path, 'wb') as temp_file:
            temp_file.write(uploaded_file.read())
        return temp_file_path, None
    except Exception as e:
        st.error(f"一時ファイル作成エラー: {e}")
        return None, None
    
# PDFファイルをダウンロードする関数
def download_file(drive, file_id):
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")  # 一時ファイルを作成
    drive.CreateFile({'id': file_id}).GetContentFile(temp_file.name, mimetype='application/pdf')
    return temp_file.name