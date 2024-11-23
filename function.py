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
from llama_index.core import download_loader, VectorStoreIndex, Settings, SimpleDirectoryReader
import tiktoken
import urllib.parse

import pytesseract
from pdf2image import convert_from_path



# 関数定義

# PDFからの１~２ページのテキスト抽出（llama_index使用）
def extract_text_from_pdf_pages(pdf_path):
    #PDFファイル読込
    reader=SimpleDirectoryReader(input_files=[pdf_path])
    all_text=reader.load_data()
    return all_text[:2]

# OCR機能を使いPDFからテキスト抽出（ページごとに実行，日本語英語対応）
def pdf_to_text_with_ocr_per_page_multi_lang(pdf_path):
    #PDFを画像に変換
    images=convert_from_path(pdf_path)
    page_texts=[]
    for image in images:
        text=pytesseract.image_to_string(image, lang='jpn+eng')
        page_texts.append(text)
    return page_texts

# PDFから全ページのテキスト抽出(llama_index + pytesseract)
def extract_text_from_pdf(pdf_path):
    #ドキュメントを開く
    reader=SimpleDirectoryReader(input_files=[pdf_path])
    documents = reader.load_data()
    ocr_cache={}
    #llama_indexで抽出したドキュメントが空の場合はOCR適用
    for doc in documents:
        if doc.text.strip() != "":
            continue
        file_path=doc.metadata['file_path']
        #ページ番号取得
        page_label = doc.metadata.get('page_label')
        #ファイルがキャッシュにない場合，OCRを実行しページごとに結果を保存
        if file_path not in ocr_cache:
            ocr_cache[file_path]=pdf_to_text_with_ocr_per_page_multi_lang(file_path)
        #各ページごとの結果をドキュメントに割り当て
        page_number=int(page_label) - 1
        if page_number < len(ocr_cache[file_path]):
            doc.text=ocr_cache[file_path][page_number] #ページ番号に対応するOCR結果を取得
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



# メタデータをデータベースに格納する関数
def store_metadata_in_db(DB_FILE,metadata,file_link,uploaded_file,drive):
    # セッションを作成
    DATABASE_URL=f"sqlite:///{DB_FILE}"
    engine=create_engine(DATABASE_URL)
    SessionLocal=sessionmaker(bind=engine)
    session = SessionLocal()
    #カテゴリ，キーワード読み込み
    categories=st.session_state["categories"]
    keywords=st.session_state["keywords"]
    
    try:
        # DOIがすでに存在するか確認
        existing_record = session.query(Metadata).filter_by(doi=metadata['doi']).first()
        
        if existing_record:
            st.warning("This DOI is already in the database.")
            return

        # カラムの幅を指定
        col1, col2 = st.columns([3, 1])

        with col1:
            st.markdown('#### アップロードしたPDF')
            # PDFの表示
            binary_data = uploaded_file.getvalue()
            pdf_viewer(input=binary_data, width=1000, height=1000)

        with col2:
            # フォームの表示
            st.markdown('#### 入力フォーム')
            with st.form(key='metadata_form'):
                selected_category = st.selectbox('関連テーマ', categories)
                selected_keywords = st.multiselect('キーワード', keywords)
                memo = st.text_area('メモ')
                read = st.checkbox('既読の場合チェック', value=False)
                submit_button = st.form_submit_button(label='保存')

            if submit_button:
                # DOI URLを生成
                doi_url = f"https://doi.org/{metadata['doi']}"

                # 選択したキーワードをカンマ区切りの文字列に変換
                selected_keywords_str = ",".join(selected_keywords)
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
                db_link = upload_db_to_google_drive(DB_FILE,drive)
                if db_link:  # データベースアップロードに成功した場合
                    st.write("データベースはGoogle Driveにアップロードされました")


                return  # 成功した場合、処理をここで終了


    except Exception as e:
        st.warning(f"An error occurred: {e}")
        session.rollback()
    finally:
        session.close()



# メタデータをデータベースに格納する関数（複数ファイル対応）
def store_metadata_in_db_batch(DB_FILE,metadata,file_link,uploaded_file):
    # セッションを作成
    DATABASE_URL=f"sqlite:///{DB_FILE}"
    engine=create_engine(DATABASE_URL)
    SessionLocal=sessionmaker(bind=engine)
    session = SessionLocal()
    #カテゴリ，キーワード読み込み
    categories=st.session_state["categories"]
    keywords=st.session_state["keywords"]
    
    try:
        # DOIがすでに存在するか確認
        existing_record = session.query(Metadata).filter_by(doi=metadata['doi']).first()
        
        if existing_record:
            st.warning("This DOI is already in the database.")
            return

        doi_url = f"https://doi.org/{metadata['doi']}"

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
            Read=False
        )

        # データベースに追加
        session.add(new_record)
        session.commit()
        st.success("New record added to the database.")

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
    

# openai apiを利用したテキストの翻訳・
def translate_and_summarize(text):
    #カテゴリ，キーワード読み込み
    categories=st.session_state["categories"]
    keywords=st.session_state["keywords"]
    # 日本語に翻訳 + 要約（OpenAIを使用）
    openai_api_key = st.secrets["openai_api_key"]
    client = OpenAI(
        # This is the default and can be omitted
        api_key=openai_api_key,
    )

    #プロンプト 要約
    prompt=f"次の論文のテキスト内容を日本語で簡潔に要約してください:\n\n{text[:1500]}"

    # GPT-4o-mini用
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )

    summary = response.choices[0].message.content.strip()

    
    #プロンプト 関連するキーワードを選択
    keyword_prompt = (
        f"次の文章に関連するキーワードを，以下のキーワードリストの語句を参考に、カンマ区切りのリストとして出力してください:\n"
        f"文章: {text}\n\n"
        f"キーワードリスト: {', '.join(keywords)}\n\n"
        "関連するキーワードをカンマで区切って出力してください:"
    )

    keyword_response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": keyword_prompt},
                ],
            }
        ],
    )
    # キーワードをカンマで分割
    keyword_res = [kw.strip() for kw in keyword_response.choices[0].message.content.strip().split('、')]

    #プロンプト 分類を選択
    category_prompt = (
        f"以下のカンマ区切りのカテゴリの語句から，この要約に最も近い語句を一つ選んで出力してください:\n"
        f"要約: {summary}\n\n"
        f"カテゴリ: {', '.join(categories)}\n\n"
    )

    category_response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": category_prompt},
                ],
            }
        ],
    )

    category_res = category_response.choices[0].message.content.strip()

    return summary, keyword_res,category_res

# Google Drive へのアップロード関数
def upload_to_google_drive(drive, uploaded_file):
    try:
        # 一時ディレクトリを作成し、ファイルを保存
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            # アップロードされたファイルの内容をバイナリモードで読み書き
            temp_file.write(uploaded_file.read())
            temp_file_path = temp_file.name  # 一時ファイルのパスを取得

        # Google Drive にアップロードするファイルメタデータを設定
        gfile = drive.CreateFile({"title": uploaded_file.name})  # uploaded_file.name を使用

        # 一時ファイルを Google Drive にアップロード
        gfile.SetContentFile(temp_file_path)
        gfile.Upload()
        st.success(f"{uploaded_file.name} をGoogle Driveにアップロードしました。")

        # アップロードしたファイルのリンクを返す
        return temp_file_path, f"https://drive.google.com/uc?id={gfile['id']}"

    except Exception as e:
        st.error(f"アップロード失敗: {e}")
        return None, None