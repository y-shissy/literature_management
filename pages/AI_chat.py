import streamlit as st
from pydrive.drive import GoogleDrive
import tempfile
import shutil
import os
from llama_index.core import StorageContext, load_index_from_storage
import openai

# ページ設定
st.set_page_config(layout="wide")

# Google Drive接続
drive = st.session_state.get("drive")

# OpenAI APIキーの設定
openai.api_key = st.secrets["openai_api_key"]

# キャッシュ変数を初期化
if "loaded_indices" not in st.session_state:
    st.session_state["loaded_indices"] = []

def load_indices_from_drive():
    """Google Driveからインデックスを読み込み、キャッシュする"""
    file_list = drive.ListFile({'q': "title contains '_index.zip' and trashed=false"}).GetList()
    if not file_list:
        st.error("インデックスファイルがGoogle Drive上に見つかりませんでした。")
        return []

    indices = []
    for index_file in file_list:
        try:
            zip_file_path = os.path.join(tempfile.gettempdir(), index_file['title'])
            index_file.GetContentFile(zip_file_path)
            extract_dir = tempfile.mkdtemp()
            shutil.unpack_archive(zip_file_path, extract_dir)

            # インデックスを読み込む
            storage_context = StorageContext.from_defaults(persist_dir=extract_dir)
            index = load_index_from_storage(storage_context)

            # 読み込んだインデックスをキャッシュに追加
            indices.append({"name": index_file["title"].replace("_index.zip", ""), "index": index})
            os.remove(zip_file_path)
        except Exception as e:
            st.error(f"インデックスファイル {index_file['title']} の読み込みに失敗しました: {str(e)}")

    return indices

def query_all_indices(prompt, indices):
    """全インデックスを統合して検索"""
    combined_results = []
    for item in indices:
        try:
            query_engine = item["index"].as_query_engine()
            result = query_engine.query(prompt)
            combined_results.append({
                "source": item["name"],
                "content": result.response,
                "metadata": result.metadata,
            })
        except Exception as e:
            st.error(f"{item['name']} でのクエリ実行に失敗しました: {str(e)}")
    return combined_results

def format_results(results):
    """結果を整形して表示"""
    # 関連性の高い順にソート
    sorted_results = sorted(results, key=lambda x: len(x["content"]), reverse=True)

    for res in sorted_results:
        with st.container():
            # カード風の見た目を作成
            st.markdown("---")  # 区切り線
            st.markdown(f"### 📘 文献名: {res['source']}")
            st.markdown(res["content"])

            # PDFファイルへのリンクを追加
            if "metadata" in res and "file_name" in res["metadata"]:
                file_name = res["metadata"]["file_name"]
                # Google Drive のリンク生成
                pdf_link = f"https://drive.google.com/file/d/{file_name}/view"
                st.markdown(f"[📄 文献を開く]({pdf_link})")

                # PDFのプレビュー機能
                if st.button(f"📖 プレビュー ({file_name})", key=file_name):
                    pdf_file_path = f"/path/to/local/pdf/{file_name}"  # 実際のパスに置き換えてください
                    pdf_viewer(pdf_file_path)

    if len(results) > 5:
        with st.expander("📚 他の関連文献を見る"):
            for res in results[5:]:
                with st.container():
                    st.markdown("---")
                    st.markdown(f"### 📘 文献名: {res['source']}")
                    st.markdown(res["content"])

                    # PDFファイルへのリンクを追加
                    if "metadata" in res and "file_name" in res["metadata"]:
                        file_name = res["metadata"]["file_name"]
                        pdf_link = f"https://drive.google.com/file/d/{file_name}/view"
                        st.markdown(f"[📄 文献を開く]({pdf_link})")

                        if st.button(f"📖 プレビュー ({file_name})", key=f"exp_{file_name}"):
                            pdf_file_path = f"/path/to/local/pdf/{file_name}"
                            pdf_viewer(pdf_file_path)


def pdf_viewer(pdf_file_path):
    """PDFをプレビューする関数"""
    try:
        with open(pdf_file_path, "rb") as pdf_file:
            st.download_button(
                label="📥 PDFをダウンロード",
                data=pdf_file,
                file_name=os.path.basename(pdf_file_path),
                mime="application/pdf",
            )
            # Streamlit 標準のPDFプレビューを使用
            st.pdf(pdf_file)
    except FileNotFoundError:
        st.error("PDFファイルが見つかりませんでした。")


def main():
    st.title(":robot_face: AI Chat")
    st.markdown("### 文献PDF情報から検索")

    if not st.session_state["loaded_indices"]:
        with st.spinner("インデックスを読み込んでいます..."):
            st.session_state["loaded_indices"] = load_indices_from_drive()

    if not st.session_state["loaded_indices"]:
        st.error("有効なインデックスが見つかりませんでした。")
        return

    indices = st.session_state["loaded_indices"]

    if st.button("リセット", use_container_width=True):
        st.session_state.messages = [{"role": "assistant", "content": "質問をどうぞ"}]
        st.experimental_rerun()

    if "messages" not in st.session_state:
        st.session_state.messages = [{"role": "assistant", "content": "質問をどうぞ"}]

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    if prompt_input := st.chat_input():
        prompt = prompt_input + "\nこの質問を日本語と英語の両方で検索し、最も関連性の高い結果を日本語で回答してください。"
        responses = query_all_indices(prompt, indices)

        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)

        if responses:
            st.session_state.messages.append({"role": "assistant", "content": "検索結果を表示中..."})
            format_results(responses)

if __name__ == "__main__":
    main()
