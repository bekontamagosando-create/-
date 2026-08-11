import os
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ 環境変数が設定されていません")
    exit(1)

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    # 軽いクエリを投げてデータベースを起こす
    res = supabase.table("system").select("*").limit(1).execute()
    print("✨ Supabaseへ正常にアクセスできました（スリープ防止成功）:", res.data)
except Exception as e:
    print(f"⚠️ アクセス中にエラーが発生しました: {e}")
    exit(1)
