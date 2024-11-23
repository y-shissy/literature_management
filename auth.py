from pydrive.auth import GoogleAuth

gauth = GoogleAuth()
gauth.LocalWebserverAuth()  # 認証用のローカルWebサーバーが起動
gauth.SaveCredentialsFile("mycreds.txt")  # 認証情報を保存
