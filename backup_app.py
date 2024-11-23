# streamlitアプリ
## 論文のPDFファイルの記載内容またはファイル名からdoi識別子を読み取り，抽出した情報をリスト化

import streamlit as st
from streamlit_pdf_viewer import pdf_viewer
import pandas as pd
import os
import re
import fitz  # PyMuPDF
import time
import requests
from langdetect import detect
from bs4 import BeautifulSoup
import sqlite3
from database import get_session, Metadata
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

    
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
import time
from openai import OpenAI

from dotenv import load_dotenv

# .envファイルのパスを指定して読み込む
load_dotenv()


#### 未使用
# from transformers import pipeline
# import shutil
# import PyPDF2
# import urllib.parse

# 初期設定
# 文献にタグ付けするカテゴリの選択肢
categories = ["軸受", "歯車", "その他の機械要素", "トライボロジー基礎","その他"]
# 文献にタグ付けするキーワードの選択肢
keywords = ["摩擦","摩耗","接触","疲労","NV","熱","トラクション","EHL","油膜","レオロジー","混合潤滑","境界潤滑","流体潤滑","転がり-すべり","転がり","すべり","点接触","線接触","面接触","粗さ","熱処理","鋼材","樹脂","添加剤","コーティング","動解析","流体解析","構造解析","熱解析","分子動力学","機械学習","電気計測","温度計測","可視化計測","その場観察"]

# ページ設定
st.set_page_config(layout="wide")

# データベース接続の設定
DATABASE_URL = "sqlite:///metadata.db"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# データベースからデータを読み込む
conn = sqlite3.connect("metadata.db")
if 'df' not in st.session_state:
    df = pd.read_sql("SELECT * FROM metadata", conn)
    st.session_state["df"] = df

# 関数定義
## PDFから全ページのテキスト抽出の関数
def extract_text_from_pdf(pdf_path):
    # PDFを開く
    doc = fitz.open(pdf_path)
    
    # 全ページのテキストを格納するための変数
    all_text = ""
    
    # 各ページをループしてテキストを抽出
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        text = page.get_text()
        all_text += text + "\n"  # 各ページのテキストを追加し、ページ間に改行を追加
    
    return all_text

## PDFからの１~２ページのテキスト抽出の関数
def extract_text_from_pdf_pages(pdf_path, pages=[0, 1]):
    doc = fitz.open(pdf_path)
    all_text = ""
    for page_num in pages:
        if page_num < len(doc):
            page = doc.load_page(page_num)
            text = page.get_text()
            all_text += text + "\n"
    return all_text

## 抽出したテキストからDOIを抽出する関数
## DOIの正規表現パターン
doi_pattern = re.compile(r'(?i)\b(?:doi[:\s]*|DOI[:\s]*|https?://(?:dx\.doi\.org/|doi\.org/))?(10\.\d{4,9}/[-._;()/:A-Z0-9]+\b)')
def extract_doi(text):
    # 正規表現でDOIを抽出
    doi_matches = doi_pattern.findall(text)
    # 最初のDOIのみを返す
    return doi_matches[0] if doi_matches else None


## PDFからのテキスト抽出＋DOI抽出の関数（上記の組み合わせ）
def process_pdf(pdf_path):
    text = extract_text_from_pdf_pages(pdf_path)
    first_doi = extract_doi(text)
    return first_doi

## DOIから情報を抽出
def get_metadata_from_doi(doi):
    # Crossref APIを利用
    crossref_url = f"https://api.crossref.org/works/{doi}"
    try:
        response = requests.get(crossref_url, timeout=10)  # タイムアウトを設定
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
        response = requests.get(jalc_url, timeout=10)
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

## メタデータをデータベースに格納する関数
def store_metadata_in_db(metadata,save_path,uploaded_file):
    # セッションを作成
    session = SessionLocal()
    
    try:
        # DOIがすでに存在するか確認
        existing_record = session.query(Metadata).filter_by(doi=metadata['doi']).first()
        
        if existing_record:
            st.warning("This DOI is already in the database.")
            return
        # 内容の要約
        # フルテキスト抽出処理
        #full_text = extract_text_from_pdf(save_path)
        # summary = summarizer(metadata['title'])[0]['summary_text']
        #summary = summarize_text(full_text)
        # キーワード抽出
        # keywords = ', '.join([keyword['word'] for keyword in keyword_extractor(metadata['title'])])
        
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
                    ファイルリンク=save_path,
                    キーワード=selected_keywords_str,
                    関連テーマ=selected_category,
                    メモ=memo,
                    Read=read
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
    # 必要ならJ-Stageなど他の検索サービスの実装も追加可能
    return None

## Ciniiからdoiを抽出する関数
def search_doi_on_cinii(filename):
    # ファイル名のクリーンアップ（例: 拡張子や不要な部分を除去）
    cleaned_filename = filename.strip()

    # CiNiiの検索URLにクリーンアップされたファイル名を使用
    search_url = f"https://cir.nii.ac.jp/opensearch/all?title={cleaned_filename}"

    try:
        # リクエストを送信してレスポンスを取得
        response = requests.get(search_url)

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

# doiのリンク先を取得（リダイレクトをフォロー）
def get_final_url(doi_url):
    try:
        # リダイレクトをフォローして最終URLを取得
        response = requests.get(doi_url, allow_redirects=True)
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
        response = requests.get(url)
        response.raise_for_status()  # ステータスコードがエラーの場合は例外を発生させる
        soup = BeautifulSoup(response.content, "lxml")
        # ページの全文を取得（HTML全体のテキスト部分を取得する方法）
        full_text = soup.get_text(separator="\n", strip=True)
        return full_text
    except requests.RequestException as e:
        print(f"URLへのアクセスに失敗しました: {e}")
        return None

# seleniumを使いウェブページから直接テキストを取得
def get_full_text_via_selenium(url):
    # Chromeドライバを自動で取得
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
    
    try:
        # URLを開く
        driver.get(url)
        time.sleep(5)  # ページの読み込みを待機（必要に応じて調整）

        # ページ全体のテキストを取得
        body = driver.find_element(By.TAG_NAME, 'body')
        full_text = body.text

        return full_text
    except Exception as e:
        print(f"エラーが発生しました: {e}")
        return None
    finally:
        # ブラウザを閉じる
        driver.quit()


def get_abstract_via_selenium(url):
    # Chromeドライバを自動で取得
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
    
    try:
        # URLを開く
        driver.get(url)
        time.sleep(5)  # ページの読み込みを待機（必要に応じて調整）

        # Abstractを取得（ScienceDirectの場合、Abstractは 'div' タグに含まれている）
        abstract_element = driver.find_element(By.CLASS_NAME, 'abstract')
        abstract = abstract_element.text

        return abstract
    except Exception as e:
        print(f"エラーが発生しました: {e}")
        return None
    finally:
        # ブラウザを閉じる
        driver.quit()

# openai apiを利用したテキストの翻訳・
def translate_and_summarize(text):
    # 日本語に翻訳 + 要約（OpenAIを使用）
    client = OpenAI(
        # This is the default and can be omitted
        api_key=os.getenv('OPENAI_API_KEY'),
    )

    #プロンプト 要約
    prompt=f"次の文章に含まれるAbstractまたは抄録の内容を日本語で簡潔に要約してください:\n\n{text}"

    # コメントアウト GPT-3.5用
    # chat_completion = client.chat.completions.create(
    #     messages=[
    #         {
    #             "role": "user",
    #             "content": f"次の文章に含まれるAbstractまたは抄録の内容を日本語で簡潔に要約してください:\n\n{text}",
    #         }
    #     ],
    #     model="gpt-3.5-turbo",
    #     max_tokens=300
    # )
    # keyword_response = client.chat.completions.create(
    #     messages=[
    #         {
    #             "role": "user",
    #             "content":keyword_prompt,
    #         }
    #     ],
    #     model="gpt-3.5-turbo",
    #     max_tokens=100
    # )
    # summary = chat_completion.choices[0].message.content.strip()


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


# メイン関数
def main():
        
    st.title(":book:文献管理アプリ")
    option = st.radio("操作を選択してください", ('データベース閲覧','PDFファイルのアップロード', 'DOIを手動入力'))
    if option == 'データベース閲覧':

        # 関連テーマカラムのユニークな値を抽出
        unique_category = st.session_state["df"]["関連テーマ"].unique()
        # journalカラムのユニークな値を抽出
        unique_journals = st.session_state["df"]["ジャーナル"].unique()
        # authorsカラムのユニークな名前を抽出（カンマ区切りで抽出）
        authors = st.session_state["df"]["著者"].str.split(',').explode().str.strip().unique()
        # keywordsカラムのユニークなキーワードを抽出（カンマ区切りで抽出）
        keywords = st.session_state["df"]["キーワード"].str.split(',').explode().str.strip().unique()

        st.markdown('#### :mag:フィルタリング項目')
        col1, col2, col3, col4, col5, col6 = st.columns(6)
        with col1:
            selected_status = st.selectbox("既読・未読", options=[None, True, False], format_func=lambda x: "すべて" if x is None else ("既読" if x else "未読"))
        with col2:
            selected_category = st.selectbox("関連テーマ", options=[None] + list(unique_category), format_func=lambda x: "すべて" if x is None else x)
        with col3:
            selected_keyword = st.selectbox("キーワード", options=[None] + list(keywords), format_func=lambda x: "すべて" if x is None else x)
        with col4:
            selected_journal = st.selectbox("ジャーナル", options=[None] + list(unique_journals), format_func=lambda x: "すべて" if x is None else x)
        with col5:
            selected_author = st.selectbox("著者", options=[None] + list(authors), format_func=lambda x: "すべて" if x is None else x)
        with col6:
            text_for_filter = st.text_input("検索語句")

        # フィルタリング条件に基づいてデータをフィルタリング
        filtered_df = st.session_state["df"].copy()

        if selected_status is not None:
            filtered_df = filtered_df[filtered_df["Read"] == selected_status]
        if selected_category is not None:
            filtered_df = filtered_df[filtered_df["関連テーマ"]  == selected_category]
        if selected_journal is not None:
            filtered_df = filtered_df[filtered_df["ジャーナル"] == selected_journal]
        if selected_author is not None:
            filtered_df = filtered_df[filtered_df["著者"].str.contains(selected_author)]
        if selected_keyword is not None:
            filtered_df = filtered_df[filtered_df["キーワード"].str.contains(selected_keyword)]
        if text_for_filter:
            filtered_df = filtered_df[filtered_df.apply(lambda row: row.astype(str).str.contains(text_for_filter, case=False).any(), axis=1)]

        # DataFrameを表示
        st.markdown('#### :open_book:文献リスト表示')
        # 表示画面用のcolumn_config設定
        column_config={'doi_url': st.column_config.LinkColumn('Web', display_text='URL'),
        'Read': st.column_config.CheckboxColumn('Read'),
        'キーワード': st.column_config.ListColumn(
            "キーワード",
            help="キーワード",
            width="medium",
        )}
        #特定カラムを表示上除外して，データを表示
        st.dataframe(filtered_df.drop(columns=['開始ページ', '終了ページ','ファイルリンク']), column_config=column_config, hide_index=True, use_container_width=True)

        st.markdown('#### :pencil:データ編集')
        # データ編集のチェックボックス
        edit_data = st.checkbox("データを編集する")
        if edit_data:
            # 編集画面用のcolumn_config設定
            column_config_edit={'doi_url': st.column_config.LinkColumn('Web', display_text='URL'),
            'Read': st.column_config.CheckboxColumn('Read'),
            "関連テーマ": st.column_config.SelectboxColumn(
                "関連テーマ",
                help="関連テーマ",
                width="medium",
                options=categories,
                required=True,
            )}
            # ユーザーが行を追加・削除できるようにする
            edited_df = st.data_editor(filtered_df, num_rows="dynamic", column_config=column_config_edit)

            # 変更を保存
            if st.button("変更を保存"):
                # 一時的なテーブルを作成（'id'列を除く）
                edited_df.to_sql("temp_metadata", conn, if_exists="replace", index=False)
                # 元データベースから削除されるべき行を特定する
                existing_ids = pd.read_sql("SELECT id FROM metadata", conn)['id']
                new_ids = edited_df['id']
                ids_to_delete = existing_ids[~existing_ids.isin(new_ids)]
                # 元のテーブルに全カラムを挿入
                with conn:
                    # 対応するidを持つ行を削除
                    if not ids_to_delete.empty:
                        conn.execute(f"DELETE FROM metadata WHERE id IN ({','.join(map(str, ids_to_delete))})")
                    # 編集したデータフレームの内容を元のデータフレームに追加
                    conn.execute("DELETE FROM metadata WHERE id IN (SELECT id FROM temp_metadata)")
                    conn.execute("""
                        INSERT INTO metadata (タイトル, 著者,ジャーナル,巻,号,開始ページ,終了ページ,年,要約,キーワード,関連テーマ,doi,doi_url,ファイルリンク,メモ,Read)
                        SELECT タイトル, 著者,ジャーナル,巻,号,開始ページ,終了ページ,年,要約,キーワード,関連テーマ,doi,doi_url,ファイルリンク,メモ,Read FROM temp_metadata
                    """)
                    conn.execute("DROP TABLE temp_metadata")

                # データを再読み込みし、セッション状態を更新
                df = pd.read_sql("SELECT * FROM metadata", conn)
                st.session_state["df"] = df
                st.success("変更が保存されました．間もなくリロードします．")
                time.sleep(3)
                st.experimental_rerun()

        st.markdown('#### :open_file_folder:ファイル表示')
        # ファイル表示のチェックボックス
        file_view = st.checkbox("PDFファイルを表示する")
        if file_view:
            # id-タイトルの形式で選択肢を作成
            options = filtered_df.apply(lambda row: f"{row['id']}-{row['タイトル']}", axis=1)

            # レコード選択のためのセレクトボックス（初期選択なし）
            selected_option = st.selectbox("PDFファイル選択", options,index=0)

            # 選択されたレコードのファイルリンクを取得
            if selected_option:
                selected_index = options[options == selected_option].index[0]
                selected_file_path = filtered_df.loc[selected_index, 'ファイルリンク']

                # PDFの表示
                if selected_file_path:
                    with open(selected_file_path, "rb") as pdf_file:
                        binary_data = pdf_file.read()
                        pdf_viewer(input=binary_data, width=1400)


    elif option == 'PDFファイルのアップロード':
        uploaded_file = st.file_uploader("PDFファイルをアップロードしてください", type=["pdf"])
        if uploaded_file:
            # 保存先フォルダのパスを指定
            save_folder = "uploaded_pdfs"
            if not os.path.exists(save_folder):
                os.makedirs(save_folder)

            save_path = os.path.join(save_folder, uploaded_file.name)

            # PDFファイルを保存
            with open(save_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            # # PDFの表示
            # binary_data = uploaded_file.getvalue()
            # pdf_viewer(input=binary_data, width=700, height=900)

            # DOI抽出処理
            doi = process_pdf(save_path)

            if not doi:
                # If DOI extraction fails, search using filename
                search_term = os.path.splitext(uploaded_file.name)[0]
                doi = search_doi_from_filename(search_term)
                st.write(f"Search term: {search_term}") 
                st.write(f"Searched DOI: {doi}") 

            if doi:
                st.success(f"DOI found: {doi}")
                #メタデータ表示
                metadata=display_metadata(doi)
                #データベースへの格納処理
                store_metadata_in_db(metadata,save_path,uploaded_file)
                #再読み込み
                st.cache_data.clear()  # キャッシュをクリア
                df = pd.read_sql("SELECT * FROM metadata", conn)  # データ再読み込み
                st.session_state["df"] = df

            else:
                st.error("DOI could not be found.")
                return  # 最後に処理を終了させる


    elif option == 'DOIを手動入力':
        doi_input = st.text_input("DOIを入力してください")
        if doi_input:
            metadata=display_metadata(doi_input)

            if metadata:
                st.success(f"DOI found: {doi_input}")

                doi_url = f"https://doi.org/{doi_input}"
                # リダイレクト先のURLを取得

                final_url = get_final_url(doi_url)
                content = get_abstract_from_url(final_url)
                if "Redirecting" in content:
                    content=get_full_text_via_selenium(doi_url)

                st.write(content)

                summary, keyword_res,category_res=translate_and_summarize(content)

                st.write(summary)
                st.write(keyword_res)
                st.write(category_res)

                uploaded_file = st.file_uploader("PDFファイルをアップロードしてください", type=["pdf"])
                if uploaded_file:
                    # 保存先フォルダのパスを指定
                    save_folder = "uploaded_pdfs"
                    if not os.path.exists(save_folder):
                        os.makedirs(save_folder)

                    save_path = os.path.join(save_folder, uploaded_file.name)

                    #データベースへの格納処理
                    store_metadata_in_db(metadata,save_path,uploaded_file)
                    #再読み込み
                    st.cache_data.clear()  # キャッシュをクリア
                    df = pd.read_sql("SELECT * FROM metadata", conn)  # データ再読み込み
                    st.session_state["df"] = df


if __name__ == "__main__":
    main()