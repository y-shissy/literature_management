from pydrive.auth import GoogleAuth

# 認証するためのGoogleAuthのインスタンスを作成
gauth = GoogleAuth()

# ローカルサーバーを介して認証
gauth.LocalWebserverAuth()  # 認証用のローカルWebサーバーが起動

# 認証情報をファイルに保存
gauth.SaveCredentialsFile("mycreds.txt")