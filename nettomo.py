import discord
from discord import ui, app_commands
from discord.ext import commands, tasks
import os
import datetime
import asyncio
import json
import shutil
from supabase import create_client, Client
from flask import Flask
from threading import Thread

# サーバー機能の作成
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run():
    app.run(host='0.0.0.0', port=8000)

def keep_alive():
    t = Thread(target=run)
    t.start()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

profiles = {}
active_trials = {} # これを追加
room_counter = 1

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'my_bot_data.json')

# --- 1. 関数定義 ---
# --- 修正版: 読み込みと保存のセット ---

def load_data():
    global profiles, room_counter, active_trials
    try:
        # profilesテーブルから全データを取得して辞書に戻す
        res = supabase.table("profiles").select("*").execute()
        profiles = {item['user_id']: item['data'] for item in res.data}

        trial_res = supabase.table("active_trials").select("*").execute()
        active_trials = {item['txt_id']: item['data'] for item in trial_res.data}
        
        # systemテーブルからroom_counterを取得
        sys_res = supabase.table("system").select("value").eq("key", "room_counter").execute()
        room_counter = sys_res.data[0]['value'] if sys_res.data else 1
        
        # active_trials は必要に応じて同様に実装
    except Exception as e:
        print(f"⚠️ DB読み込みエラー: {e}")
        profiles, room_counter, active_trials = {}, 1, {}
    return profiles


def save_data():
    global profiles, room_counter, active_trials
    
    # 1. profiles の保存
    for u_id, data in profiles.items():
        supabase.table("profiles").upsert({"user_id": str(u_id), "data": data}).execute()
    
    # 2. active_trials の保存
    for txt_id, data in active_trials.items():
        supabase.table("active_trials").upsert({"txt_id": str(txt_id), "data": data}).execute()
    
    # 3. room_counter の保存
    supabase.table("system").upsert({"key": "room_counter", "value": room_counter}).execute()

# --- 2. データの読み込み ---
load_data() # 関数を定義した後に呼び出す
user_message_history = {}
# --- 3. 設定値 ---
SETTING_CH_ID = 1496129777776726167
RECRUIT_CH_ID = 1495410725630513211
PROFILE_CH_ID = 1496123473167257721
ROLE_NAME = "プロフ済"
ADMIN_USER_ID = 968461296334929973
LOG_CH_ID = 1523629114555236393
RECRUIT_LOG_CH_ID = 1524381456506425354
# --- 4. Bot初期化 ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

keep_alive()

TOKEN = os.getenv("DISCORD_TOKEN")

async def delayed_delete(msg_list, delay):
    await asyncio.sleep(delay)
    for m in msg_list:
        try: await m.delete()
        except: pass

# ==========================================
# 2. 交流ルーム管理
# ==========================================

class RoomControlView(ui.View):
    def __init__(self, txt_id: int, vc_id: int):
        super().__init__(timeout=None)
        self.txt_id = txt_id
        self.vc_id = vc_id

    @ui.button(label="運営を呼ぶ（緊急）", style=discord.ButtonStyle.danger, emoji="🚨", custom_id="persistent:room_report")
    async def report_room(self, it: discord.Interaction, btn: ui.Button):
        # 1. ユーザーへの通知（フィードバック）
        await it.response.send_message("🚨 管理者に緊急通報しました。対応までお待ちください。", ephemeral=True)
        
        # 2. 自分（管理者）にDMを送信
        try:
            admin_user = await it.client.fetch_user(ADMIN_USER_ID)
            if admin_user:
                report_embed = discord.Embed(title="🚨 【緊急】交流ルームからの呼び出し", color=0xff0000)
                report_embed.add_field(name="通報者", value=it.user.mention, inline=True)
                report_embed.add_field(name="部屋", value=it.channel.mention, inline=False)
                report_embed.add_field(name="サーバー", value=it.guild.name, inline=False)
                
                # 部屋へ直接飛べるボタンを付けておく
                view = discord.ui.View()
                view.add_item(discord.ui.Button(label="部屋へ移動", url=it.channel.jump_url))
                
                await admin_user.send(embed=report_embed, view=view)
        except Exception as e:
            print(f"DM送信失敗: {e}")

    @ui.button(label="交流を終了する（部屋削除）", style=discord.ButtonStyle.danger, emoji="🧹", custom_id="persistent:room_delete")
    async def delete_room(self, it: discord.Interaction, btn: ui.Button):
        await it.response.send_message("🧹 部屋とVCを削除しています...", ephemeral=True)
        
        try:
            # 1. チャンネル名（例: "chat-021"）を取得
            target_name = it.channel.name
            
            # 2. サーバー内の全VCをチェックして、名前が一致するものを消す
            for vc in it.guild.voice_channels:
                if vc.name == target_name: # 名前が一致すれば確実にそれがペアのVC
                    await vc.delete(reason="ユーザーによる終了")
            
            # 3. テキストチャンネルを削除
            await it.channel.delete(reason="ユーザーによる終了")
                
        except Exception as e:
            print(f"DEBUG: 削除エラー: {e}")
            await it.followup.send(f"❌ 削除失敗: {e}", ephemeral=True)
# ==========================================
# 3. 掲示板システム
# ==========================================

class AgreeView(ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Bot再起動後も消えないように設定

    @ui.button(label="利用規約に同意して参加する", style=discord.ButtonStyle.primary, custom_id="persistent:agree_button")
    async def agree_btn(self, it: discord.Interaction, btn: ui.Button):
        # ★あなたのサーバーの「Member」ロールIDに変更してください
        MEMBER_ROLE_ID = 1507030726736871687
        role = it.guild.get_role(MEMBER_ROLE_ID)
        
        if not role:
            await it.response.send_message("❌ 設定エラー：指定のロールが見つかりません。", ephemeral=True)
            return

        # すでにロールを持っているか確認
        if role in it.user.roles:
            await it.response.send_message("✅ あなたはすでに同意済みです。", ephemeral=True)
        else:
            await it.user.add_roles(role)
            await it.response.send_message("✅ 同意が完了しました！チャンネルが解放されます。", ephemeral=True)

class RecruitView(ui.View):
    # 1. timeout=None を明示する
    def __init__(self, author_id: int = 0, target_num: int = 0):
        super().__init__(timeout=None) 
        self.author_id = author_id
        self.target_num = target_num
        self.joined_users = []

        # 2. すべてのボタンに custom_id を設定する
        # (すでに設定済みかもしれませんが、念のため)
        for item in self.children:
            if isinstance(item, ui.Button):
                if item.label == "挨拶してみる":
                    item.custom_id = "recruit:join"
                elif item.label == "募集主のプロフを見る":
                    item.custom_id = "recruit:prof"

    @ui.button(label="募集主のプロフを見る", style=discord.ButtonStyle.secondary, emoji="📑", custom_id="recruit:join")
    async def view_prof(self, it: discord.Interaction, btn: ui.Button):
        # 🚨 再起動対策：もし author_id が消えていたら、メッセージの埋め込み（Embed）から自動復元する
        author_id = self.author_id
        if author_id is None and it.message.embeds:
            emb = it.message.embeds[0]
            # user_data に登録されているユーザーの中から、Embedのタイトルに display_name が含まれる人を探す
            for key in profiles.keys():
                member = it.guild.get_member(int(key))
                if member and member.display_name in emb.title:
                    author_id = int(key)
                    break

        if not author_id:
            return await it.response.send_message("⚠️ システム再起動のため、募集主の特定ができませんでした。", ephemeral=True)

        # データの読み込み
        data = profiles.get(str(author_id))
        if not data or "gender" not in data: # プロフデータが空、またはactive_trialsのデータしか無い場合を弾く
            return await it.response.send_message("⚠️ 募集主のプロフが見つかりません。（未登録か、データが壊れています）", ephemeral=True)
        
        member = it.guild.get_member(author_id)
        embed = discord.Embed(title=f"📑 {member.display_name if member else '不明'} さんの取説", color=0x9b59b6)
        
        # 辞書のキー（data['gender']など）は、保存した時と同じ名前にしてください
        embed = discord.Embed(title=f"📑 {member.display_name if member else '不明'} さんの取説", color=0x9b59b6)
        
        # 1. 基本情報は横並びにしてスッキリさせる
        embed.add_field(name="性別・年齢", value=data['gender'], inline=True)
        embed.add_field(name="主な機種", value=data['platform'], inline=True)
        
        # 2. ゲームとスタイルは独立させる
        embed.add_field(name="🎮 主なゲーム", value=data['game'], inline=False)
        embed.add_field(name="🤝 スタイル・頻度", value=data['style'], inline=False)
        
        # 3. メッセージは最後に配置
        embed.add_field(name="💬 自己紹介・ひとこと", value=data['intro'], inline=False)
        
        if member:
            embed.set_thumbnail(url=member.display_avatar.url)
            
        await it.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="挨拶してみる", style=discord.ButtonStyle.success, emoji="✉️", custom_id="recruit:prof")
    async def join_room(self, it: discord.Interaction, btn: ui.Button):
        global room_counter

        # 1. 満員チェック
        if len(self.joined_users) >= self.target_num:
            try: await it.message.delete()
            except: pass
            return await it.response.send_message("この募集はすでに満員です。", ephemeral=True)

        if it.user.id == self.author_id:
            return await it.response.send_message("自分自身には挨拶できません。", ephemeral=True)

        if it.user.id in self.joined_users:
            return await it.response.send_message("既に参加しています。", ephemeral=True)

        await it.response.defer(ephemeral=True)

        # 2. 参加者リストに追加 (1回のみにする)
        self.joined_users.append(it.user.id) 
        remaining = self.target_num - len(self.joined_users)
        
        # 3. Embedの人数表示を更新
        if it.message.embeds:
            new_embed = it.message.embeds[0].copy()
            for i, field in enumerate(new_embed.fields):
                if field.name == "👥 募集人数":
                    new_embed.set_field_at(i, name="👥 募集人数", value=f"{self.target_num}名 (残り: {max(0, remaining)}名)", inline=True)
                    break
            await it.message.edit(embed=new_embed)

        # 4. 部屋作成処理
        room_name = f"chat-{room_counter:03}"
        room_counter += 1
        save_data()

        overwrites = {
            it.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            it.user: discord.PermissionOverwrite(view_channel=True, connect=True, send_messages=True),
            it.guild.get_member(self.author_id): discord.PermissionOverwrite(view_channel=True, connect=True, send_messages=True),
            it.guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True)
        }

        # カテゴリを取得
        cat = it.channel.category
        
        # 名前を統一する (例: "room-021")
        common_name = f"room-{room_counter:03}"
        
        # テキストとVCを同じ名前で作成
        txt = await it.guild.create_text_channel(name=common_name, overwrites=overwrites, category=cat)
        vc = await it.guild.create_voice_channel(name=common_name, overwrites=overwrites, category=cat)

        # --- 修正箇所：ログ取得部分 ---
        # マッチング成立時の送信処理
        recruit_log_ch = it.guild.get_channel(RECRUIT_LOG_CH_ID)

        if recruit_log_ch:
            log_embed = discord.Embed(title="🔗 マッチング成立！", color=0x3498db)
    
            # ★【ここが重要】募集元のチャンネルではなく、新しく作った txt へのリンクにする
            log_embed.add_field(name="募集部屋", value=f"<#{txt.id}>", inline=True)
    
            # 募集主と応募者
            log_embed.add_field(name="募集主", value=f"{it.guild.get_member(self.author_id).mention}", inline=True)
            log_embed.add_field(name="応募者", value=f"{it.user.mention}", inline=True)
    
            await recruit_log_ch.send(embed=log_embed)

        # 6. ルーム案内
        start_embed = discord.Embed(title="✨ 交流ルームが作成されました！", color=0x3498db)
        await txt.send(content=f"{it.guild.get_member(self.author_id).mention} {it.user.mention}", embed=start_embed, view=RoomControlView(txt.id, vc.id))

        rule_embed = discord.Embed(
            title="📜 交流ルームのルール", 
            description=(
                "気持ちよく交流するために、以下のルールを守りましょう。\n\n"
                "1. **暴言・誹謗中傷は禁止**です。\n"
                "2. **個人情報の取り扱いに注意**してください。\n"
                "3. **個人のDMはトラブル防止のため禁止**としています。\n"
                "4. 相手への敬意と配慮を忘れないようにしましょう。\n\n"
                "何かトラブルがあった場合は、下の「運営を呼ぶ」ボタンを押してください。"
            ), 
            color=0xf1c40f
        )
        await txt.send(embed=rule_embed)

        # 8. 最終処理（メッセージ削除 or 完了通知）
        if len(self.joined_users) >= self.target_num:
            try: 
                await it.message.delete()
            except: 
                pass
            await it.followup.send(f"定員に達したため募集を終了しました。部屋: {txt.mention}", ephemeral=True)
        else:
            await it.followup.send(f"専用部屋 {txt.mention} を作成しました！", ephemeral=True)
# ==========================================
# 4. 入力フォーム (Modal) - 完全完結5項目
# ==========================================

class PostModal(ui.Modal, title="📢 ネッ友募集掲示板"):
    # info1〜info4 を募集用のラベルに合わせる
    purpose = ui.TextInput(label="1. 募集目的", placeholder="例：雑談できる友達がほしい、○○を一緒にやりたい")
    info4 = ui.TextInput(label="2. 希望スタイル・頻度", placeholder="例：エンジョイ・毎日通話、ガチ・週末のみ")
    info2 = ui.TextInput(label="3. 指定機種・条件", placeholder="例：PCの方、Switchの方など")
    count = ui.TextInput(label="4. 募集人数 (数字のみ)", default="1", min_length=1, max_length=2)
    intro = ui.TextInput(label="5. 自己紹介・ひとこと", style=discord.TextStyle.paragraph)

    async def on_submit(self, it: discord.Interaction):

        try:
            target_num = int(self.count.value)
        except ValueError:
            return await it.response.send_message("❌ 募集人数には半角数字を入力してください。", ephemeral=True)

        embed = discord.Embed(title=f"🤝 {it.user.display_name} さんの友達募集", color=0x2ecc71)
        embed.add_field(name="🎯 募集目的", value=self.purpose.value, inline=False)
        embed.add_field(name="🎮 スタイル・頻度", value=self.info4.value, inline=True)
        embed.add_field(name="⚙️ 指定機種", value=self.info2.value, inline=True)
        embed.add_field(name="👥 募集人数", value=f"{target_num}名", inline=True)
        embed.add_field(name="📝 自己紹介", value=self.intro.value, inline=False)
        
        recruit_ch = it.guild.get_channel(RECRUIT_CH_ID)
        if recruit_ch:
            await recruit_ch.send(embed=embed, view=RecruitView(it.user.id, target_num))
            await it.response.send_message("✅ 掲示板に投稿しました！", ephemeral=True)
        else:
            await it.response.send_message("⚠️ 募集用チャンネルが見つかりません。設定を確認してください。", ephemeral=True)

class BasicModal(ui.Modal, title="👤 プロフィール作成"):
    # 変数名を統一感のあるものにしました
    gender = ui.TextInput(label="1. 性別・年齢", placeholder="例：男・20代、女・社会人")
    platform = ui.TextInput(label="2. 主な機種", placeholder="例：PC, Switch, スマホ")
    game = ui.TextInput(label="3. 主なゲーム", placeholder="例：Apex, 原神, モンハン")
    style = ui.TextInput(label="4. スタイル・活動頻度", placeholder="例：エンジョイ・毎日通話、まったり・週末のみ")
    intro = ui.TextInput(label="5. 自己紹介・ひとこと", style=discord.TextStyle.paragraph)

    async def on_submit(self, it: discord.Interaction):
        await it.response.defer(ephemeral=True)
        global profiles
        
        # データをまとめる
        data = { # ここを full_data ではなく data にすると簡単です
            "gender": self.gender.value,
            "platform": self.platform.value,
            "game": self.game.value,
            "style": self.style.value,
            "intro": self.intro.value
        }
        
        # 保存処理
        profiles[str(it.user.id)] = data
        save_data()
        
        # ロール付与
        role = discord.utils.get(it.guild.roles, name=ROLE_NAME)
        if role: 
            await it.user.add_roles(role)

        # プロフィールチャンネルの取得
        profile_ch = it.guild.get_channel(PROFILE_CH_ID)
        
        # 🚨【新機能】過去に自分が投稿した古いプロフメッセージを検索して削除
        if profile_ch:
            try:
                # 直近100件のメッセージをさかのぼってチェック
                async for msg in profile_ch.history(limit=100):
                    # 「Botが送信したメッセージ」かつ「Embed（埋め込み）がある」かつ「タイトルに自分の名前が含まれている」
                    if msg.author == bot.user and msg.embeds:
                        embed_title = msg.embeds[0].title
                        if it.user.display_name in embed_title:
                            await msg.delete() # 古いプロフを削除
                            await asyncio.sleep(0.5) # エラー防止用の短いウェイト
                            break # 見つかったらループ終了
            except Exception as e:
                print(f"過去のプロフ削除中にエラーが発生しました: {e}")

        # 新しいプロフィールチャンネルへ投稿するEmbed
        embed = discord.Embed(title=f"👤 {it.user.display_name}の図鑑プロフ", color=0x3498db)
        
        # 1. 性別・年齢
        embed.add_field(name="📌 性別・年齢", value=data.get('gender', '未設定'), inline=True)
        # 2. 主な機種
        embed.add_field(name="🎮 主な機種", value=data.get('platform', '未設定'), inline=True)
        # 3. 主なゲーム
        embed.add_field(name="🕹️ 主なゲーム", value=data.get('game', '未設定'), inline=False)
        # 4. スタイル・活動頻度 (追加しました)
        embed.add_field(name="🤝 スタイル・活動頻度", value=data.get('style', '未設定'), inline=False)
        # 5. 自己紹介
        embed.add_field(name="💬 自己紹介", value=data.get('intro', '未設定'), inline=False)
        
        embed.set_thumbnail(url=it.user.display_avatar.url)
        
        if profile_ch:
            await profile_ch.send(embed=embed)
        
        # 🚨 修正：it.response.send_message ではなく it.followup.send を使う！
        await it.followup.send(
            "✅ **プロフィール登録が完了しました！**\n\n"
            "これで募集チャンネルにアクセスできるようになりました。\n"
            "さっそく「募集チャンネル」を見て、気の合う人を探してみましょう！",
            ephemeral=True
        )

# ==========================================
# 5. メインパネル & 通報
# ==========================================

class PostStartView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="募集を出す", style=discord.ButtonStyle.primary, custom_id="post_start")
    async def post_button(self, it: discord.Interaction, button: discord.ui.Button):
        await it.response.send_modal(PostModal())

class ProfileStartView(ui.View):
    def __init__(self): super().__init__(timeout=None)
    @ui.button(label="📝 プロフ作成/更新", style=discord.ButtonStyle.primary, emoji="✨", custom_id="p:start_prof")
    async def prof(self, it, btn):
        if it.channel_id != SETTING_CH_ID: return await it.response.send_message(f"プロフ作成は <#{SETTING_CH_ID}> で！", ephemeral=True)
        await it.response.send_modal(BasicModal())

class ReportStartView(ui.View):
    def __init__(self): super().__init__(timeout=None)
    @ui.button(label="🛡️ 相談・通報ルームを作成する", style=discord.ButtonStyle.danger, emoji="⚠️", custom_id="p:start_ticket")
    async def ticket(self, it: discord.Interaction, btn: ui.Button):
        await it.response.defer(ephemeral=True)
        channel_name = f"🛡️相談-{it.user.display_name}"
        overwrites = {
            it.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            it.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True),
            it.guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True, send_messages=True)
        }
        cat = it.channel.category
        ticket_ch = await it.guild.create_text_channel(name=channel_name, overwrites=overwrites, category=cat)
        
        embed = discord.Embed(
            title="🛡️ 個別相談・通報窓口",
            description=(
                f"{it.user.mention} さん、この部屋はあなたと管理者だけが見れる専用ルームです。\n\n"
                "**【教えてほしいこと】**\n"
                "● **証拠のスクリーンショット**（ここに貼り付けてください）\n"
                "● 相手の名前、または起きた時間や場所\n"
                "● どのようなトラブルがあったか\n\n"
                "管理者が確認するまでそのままお待ちください。\n"
                "完了したら、下のボタンを押して部屋を閉じてください。"
            ),
            color=0xff0000
        )
        await ticket_ch.send(embed=embed, view=TicketControlView())
        await it.followup.send(f"専用ルーム {ticket_ch.mention} を作成しました。こちらで相談内容を記入してください。", ephemeral=True)

class TicketControlView(ui.View):
    def __init__(self): super().__init__(timeout=None)
    
    @ui.button(label="相談を終了してこの部屋を閉じる", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket:close")
    async def close(self, it: discord.Interaction, btn: ui.Button):
        # 👑 実行者が「管理者(administrator)」または「チャンネル管理(manage_channels)」の権限を持っているかチェック
        if not (it.user.guild_permissions.administrator or it.user.guild_permissions.manage_channels):
            return await it.response.send_message("⚠️ このボタンは管理者（運営スタッフ）のみが使用できます。", ephemeral=True)
            
        await it.response.send_message("お疲れ様でした。このチャンネルを5秒後に削除します。")
        await asyncio.sleep(5)
        await it.channel.delete()

class JudgementView(discord.ui.View):
    """サーバー主のDMに表示される判決ボタンの処理クラス"""
    def __init__(self, guild_id: int, target_user_id: int, reason: str):
        # 👇 ここを「custom_id」が正しく紐づくように少しだけ修正します！
        super().__init__(timeout=None) 
        self.guild_id = guild_id
        self.target_user_id = target_user_id
        self.reason = reason

    async def get_target_and_guild(self, interaction: discord.Interaction):
        guild = bot.get_guild(self.guild_id)
        if not guild:
            await interaction.response.send_message("⚠️ 該当のサーバーが見つかりませんでした。", ephemeral=True)
            return None, None
        try:
            member = await guild.fetch_member(self.target_user_id)
            return member, guild
        except discord.NotFound:
            return None, guild

    # 🔨 BANする（赤色ボタン）
    @discord.ui.button(label="🔨 BANする", style=discord.ButtonStyle.danger, custom_id="judge_ban")
    async def ban_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        member, guild = await self.get_target_and_guild(interaction)
        if not guild: return

        if not member:
            await interaction.response.send_message("⚠️ 対象のユーザーは既にサーバーにいません。", ephemeral=True)
            return

        try:
            # 1. サーバーからBANを実行
            await member.ban(reason=f"累計5ポイント到達に伴うサーバー主の判決。最終理由: {self.reason}")
            
            # 2. DM画面を切り替え
            embed = interaction.message.embeds[0]
            embed.title = "🔨 【判決：死刑（BAN）】処罰を執行しました"
            embed.color = discord.Color.purple()
            embed.set_footer(text=f"執行日時: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
            
            # 3. ボタンをすべて無効化（グレーアウト）
            for child in self.children:
                child.disabled = True
            
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            await interaction.response.send_message(f"⚠️ BANの執行に失敗しました: `{e}`", ephemeral=True)

    # 👀 様子見（キープ）（青色ボタン）
    @discord.ui.button(label="👀 様子見（キープ）", style=discord.ButtonStyle.primary, custom_id="judge_watch")
    async def watch_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        member, guild = await self.get_target_and_guild(interaction)
        if not guild: return

        # 💾 データ保存
        data = load_data()
        u_id = str(self.target_user_id)
        if u_id in data:
            data[u_id]["report_count"] = 5
            save_data()

        # ★ロール整理と付与
        if member:
            # 付与したいロールを取得
            target_role = discord.utils.get(guild.roles, name="通報4回")
            
            # 「通報」を含むロールをすべて特定して削除
            roles_to_remove = [r for r in member.roles if "通報" in r.name]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove)
            
            # 新しいロールを付与
            if target_role:
                await member.add_roles(target_role)

        # DM画面を切り替え
        embed = interaction.message.embeds[0]
        embed.title = "👀 【判決：様子見】ポイントを5点（黒）に据え置きました"
        embed.color = discord.Color.blue()
        embed.set_footer(text=f"判定日時: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        for child in self.children:
            child.disabled = True
            
        await interaction.response.edit_message(embed=embed, view=self)

    # ✅ セーフ（減算）（緑色ボタン）
    @discord.ui.button(label="✅ セーフ（減算）", style=discord.ButtonStyle.success, custom_id="judge_safe")
    async def safe_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        member, guild = await self.get_target_and_guild(interaction)
        if not guild: return

        # 💾 データ保存（ポイント減算）
        data = load_data()
        u_id = str(self.target_user_id)
        current_points = 5
        if u_id in data:
            current_points = data[u_id].get("report_count", 5)
            new_points = max(0, current_points - 2)
            data[u_id]["report_count"] = new_points
            save_data()
            
        else:
            new_points = 3

        # ★ロール整理と付与
        if member:
            target_role = discord.utils.get(guild.roles, name="通報3回")
            
            # 「通報」を含むロールをすべて特定して削除
            roles_to_remove = [r for r in member.roles if "通報" in r.name]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove)
            
            # 新しいロールを付与
            if target_role:
                await member.add_roles(target_role)

        # DM画面を切り替え
        embed = interaction.message.embeds[0]
        embed.title = f"✅ 【判決：セーフ】ポイントを2点マイナスし、現在の累計を {new_points}点（赤）にしました"
        embed.color = discord.Color.green()
        embed.set_footer(text=f"判定日時: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        for child in self.children:
            child.disabled = True
            
        await interaction.response.edit_message(embed=embed, view=self)

class ObjectionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📢 異議申し立て（反論）をする", style=discord.ButtonStyle.danger, custom_id="button:objection")
    async def objection_button(self, it: discord.Interaction, button: discord.ui.Button):
        YOUR_USER_ID = 968461296334929973 
        try:
            my_user = await it.client.fetch_user(YOUR_USER_ID)
            if my_user:
                owner_embed = discord.Embed(title="📢 【異議申し立て】通知", color=0xffaa00)
                owner_embed.add_field(name="申請者", value=f"{it.user.mention}\n{it.user.name} ({it.user.id})")
                owner_embed.set_footer(text="対象のユーザーが通報に対して反論を希望しています。")
                await my_user.send(embed=owner_embed)
                
            await it.response.send_message(
                "✅ 異議申し立てのリクエストをサーバー主へ送信しました。\n"
                "運営が確認次第ご連絡いたしますので、反論内容を準備してお待ちください。", 
                ephemeral=True
            )
        except Exception as e:
            await it.response.send_message("⚠️ 申請の送信中にエラーが発生しました。", ephemeral=True)

# ==========================================
# 6. Botクラス & 起動
# ==========================================

# ==========================================
# 6. Botクラス & 起動
# ==========================================

# 2. 永続Viewの登録用 (setup_hook)
@bot.event
async def setup_hook():
    # 1. 起動時にまずデータを読み込む（これで profiles や active_trials に値が入ります）
    load_data()
    
    # 2. 基本的なViewの登録
    bot.add_view(AgreeView())
    bot.add_view(RoomControlView(0, 0))
    bot.add_view(RecruitView())
    bot.add_view(PostStartView())
    bot.add_view(ProfileStartView())
    bot.add_view(ReportStartView())
    bot.add_view(TicketControlView())
    bot.add_view(ObjectionView())

    # 3. 実行中の部屋（active_trials）の再登録
    # ★ここを修正：user_data ではなく、読み込まれた active_trials を使う
    global active_trials 
    for txt_id, data in active_trials.items():
        bot.add_view(TrialEndView(
            txt_id=int(txt_id), 
            vc_id=data.get("vc_id", 0),
            author_id=data.get("author_id", 0),
            target_id=data.get("user_id", 0)
        ))
    
    print("✨ 永続ボタンとコマンドを同期し、データを読み込みました")

# 修正したタスク部分
@tasks.loop(minutes=5) # テストのために短くしました
async def cleanup_recruit_board():
    # IDを明示的に整数として扱う
    channel = bot.get_channel(int(RECRUIT_CH_ID))
    
    # ログ出力（Renderのログを見てください）
    if not channel:
        print(f"DEBUG: チャンネルが見つかりません。ID: {RECRUIT_CH_ID}")
        return
    else:
        print(f"DEBUG: 掲示板チャンネル取得成功: {channel.name}")

    now = datetime.datetime.now(datetime.timezone.utc)
    # 古いメッセージを確実に取得するため、limit=None でループ
    async for message in channel.history(limit=None):
        # 60秒以上経過したBotのメッセージを削除
        if message.author == bot.user and (now - message.created_at).total_seconds() > 60:
            try:
                await message.delete()
                print(f"🧹 削除成功: {message.id}")
                await asyncio.sleep(1)
            except Exception as e:
                print(f"⚠️ 削除エラー: {e}")

@bot.event
async def on_ready():
    cleanup_recruit_board.start() # ★これを忘れないように！
    print(f"Logged in as {bot.user}")
    print("✅ 掲示板自動クリーンアップタスクを開始しました")
    # load_data() は setup_hook に移動したので不要
    print(f"✨ {bot.user} が起動しました！")
    await bot.change_presence(activity=discord.Game(name="募集の管理中..."))


# ==========================================
# ログ・荒らし対策用イベント（統合済み）
# ==========================================


LOG_CH_ID = 1524007213046431815 # ログを吐き出す場所
EXCLUDE_CH_IDS = [1495410725454348495, 1495410725936693319,1495410725630513211]      # ログ除外チャンネルのIDリスト

thread_map = {} 

@bot.event
async def on_message(message):
    # 1. 除外判定
    if message.author.bot or isinstance(message.channel, discord.DMChannel):
        await bot.process_commands(message)
        return
    
    # ログチャンネル自体や除外リストは処理を飛ばす
    if message.channel.id == LOG_CH_ID or message.channel.id in EXCLUDE_CH_IDS:
        await bot.process_commands(message)
        return

    # 2. ログチャンネルの取得
    log_ch = bot.get_channel(LOG_CH_ID)
    
    # 3. 【特別処理】募集チャンネルの場合：専用チャンネルへ送信
    if message.channel.id == RECRUIT_CH_ID:
        recruit_log_ch = bot.get_channel(RECRUIT_LOG_CH_ID)
        if recruit_log_ch:
            embed = discord.Embed(title="🆕 新規募集", description=message.content, color=discord.Color.green())
            embed.add_field(name="元の投稿", value=f"[ここをクリック](https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id})", inline=False)
            await recruit_log_ch.send(embed=embed)
        else:
            print(f"エラー: 募集ログチャンネル({RECRUIT_LOG_CH_ID})が見つかりません")

    # 4. 【通常処理】それ以外：スレッドへ会話ログを送信
    elif log_ch:
        target_thread = await get_log_thread(message.channel, log_ch)
        if target_thread:
            embed = discord.Embed(description=message.content or "（画像・スタンプ）", color=discord.Color.light_gray(), timestamp=message.created_at)
            embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
            await target_thread.send(embed=embed)

    await bot.process_commands(message)
# ログ用スレッド取得関数 (チャンネル名でスレッドを特定)
# スレッド管理用を一度クリア（Bot再起動でリセットされます）
thread_map = {} 

async def get_log_thread(channel, log_ch):
    channel_id = channel.id
    
    # 1. 既存のスレッドを探す
    for thread in log_ch.threads:
        if thread.name == channel.name:
            return thread
            
    # 2. 一致するものがない場合、作成する
    # 「募集一覧」という名前なら「募集ログ-元のチャンネル名」に変更して作成する
    new_name = channel.name if "募集一覧" not in channel.name else f"募集ログ-{channel.name}"
    
    try:
        thread, _ = await log_ch.create_thread(
            name=new_name,
            content=f"チャンネル #{channel.name} の活動記録",
            auto_archive_duration=1440
        )
        return thread
    except Exception as e:
        print(f"スレッド作成エラー: {e}")
        return None

@bot.event
async def on_message_edit(before, after):
    if before.author.bot or before.channel.id in EXCLUDE_CH_IDS or before.content == after.content:
        return
    log_ch = bot.get_channel(LOG_CH_ID)
    if log_ch:
        # before ではなく before.channel を渡す
        target_thread = await get_log_thread(before.channel, log_ch)
        embed = discord.Embed(title="✏️ メッセージが編集されました", color=discord.Color.orange())
        embed.add_field(name="変更前", value=before.content or "（なし）", inline=False)
        embed.add_field(name="変更後", value=after.content or "（なし）", inline=False)
        embed.set_author(name=before.author.display_name, icon_url=before.author.display_avatar.url)
        await target_thread.send(embed=embed)

@bot.event
async def on_message_delete(message):
    # Bot自身の削除や、ログ除外チャンネルは無視
    if message.author.bot or message.channel.id in EXCLUDE_CH_IDS:
        return
        
    log_ch = bot.get_channel(LOG_CH_ID)
    if log_ch:
        # スレッドの特定（既存のものがあればそれを使う）
        target_thread = await get_log_thread(message.channel, log_ch)
        
        embed = discord.Embed(title="🗑️ メッセージが削除されました", color=discord.Color.red())
        embed.add_field(name="削除された内容", value=message.content or "（画像または空のメッセージ）", inline=False)
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        embed.set_footer(text=f"チャンネル: #{message.channel.name}")
        
        if target_thread:
            await target_thread.send(embed=embed)


# テスト用コマンド
@bot.command()
async def test(ctx):
    await ctx.send("コマンドは正常に動いています！")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⚠️ 落ち着いて！あと {error.retry_after:.1f} 秒待ってね。")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ その操作をする権限がありません。")
    else:
        # 想定外のエラーをログに出してBotが落ちるのを防ぐ
        print(f"予期せぬエラー発生: {error}")

@bot.tree.error
async def on_app_command_error(it: discord.Interaction, error: app_commands.AppCommandError):
    # 一般ユーザーが「管理者限定」のスラッシュコマンドを勝手に実行した場合
    if isinstance(error, app_commands.MissingPermissions):
        await it.response.send_message("❌ このコマンドを実行する権限（管理者権限）がありません。", ephemeral=True)
    else:
        # その他の想定外のエラーが起きてもBotがクラッシュして落ちないようにキャッチする
        print(f"⚠️ コマンドエラーが発生しました: {error}")
        try:
            if not it.response.is_done():
                await it.response.send_message("⚠️ コマンドの実行中にエラーが発生しました。", ephemeral=True)
        except:
            pass

@bot.event
async def on_member_remove(member):
    if str(member.id) in profiles:
        del profiles[str(member.id)]
        save_data()
        print(f"退会したユーザーのデータを削除しました: {member.id}")



# ==========================================
# 2. 各種スラッシュコマンドの設定
# ==========================================

# --- 管理用コマンドセクションに配置 ---

@bot.tree.command(name="sync", description="管理用：コマンドを同期する")
@app_commands.checks.has_permissions(administrator=True)
async def sync(it: discord.Interaction):
    await it.response.defer(ephemeral=True)
    await bot.tree.sync()
    await it.followup.send("✅ コマンドを同期しました！")

@bot.tree.command(name="setup_profile", description="管理用：プロフ作成パネルを設置")
@app_commands.checks.has_permissions(administrator=True)
async def setup_p(it: discord.Interaction):
    embed = discord.Embed(title="✨ プロフィール作成", description="下のボタンからプロフを登録してチャンネルを解放しよう！", color=0x3498db)
    await it.response.send_message(embed=embed, view=ProfileStartView())

@bot.tree.command(name="setup_post", description="管理用：募集ボタンを設置")
@app_commands.checks.has_permissions(administrator=True)
async def setup_m(it: discord.Interaction):
    embed = discord.Embed(title="📢 友達募集を出す", description="募集を出したい時は下のボタンを押してね！", color=0x2ecc71)
    await it.response.send_message(embed=embed, view=PostStartView())

@bot.tree.command(name="setup_report", description="管理用：通報・相談パネルを設置")
@app_commands.checks.has_permissions(administrator=True)
async def setup_r(it: discord.Interaction):
    embed = discord.Embed(
        title="🚨 通報・相談窓口", 
        description="不快な行為やトラブルがあった際は、下のボタンを押してください。\nあなたと管理者だけが見れる**専用の相談ルーム**が作成されます。\n\n", 
        color=0x2f3136
    )
    await it.response.send_message(embed=embed, view=ReportStartView())

@bot.tree.command(name="report_count", description="管理用：ユーザーに違反ポイントを付与し、累計5ポイントで管理者に判決を仰ぎます")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    user="対象のメンバー",
    points="付与するポイント（1:軽度, 2〜3:中度, 5:一発アウト・即判決へ）",
    reason="処罰・警告の具体的な理由を入力してください"
)
@app_commands.choices(points=[
    app_commands.Choice(name="1ポイント（軽い注意・ちっさいこと）", value=1),
    app_commands.Choice(name="2ポイント（中度の違反・軽い暴言など）", value=2),
    app_commands.Choice(name="3ポイント（重度の違反・ひどい暴言など）", value=3),
    app_commands.Choice(name="5ポイント（一発アウト・即判決対象）", value=5)
])
async def report_count(it: discord.Interaction, user: discord.Member, points: int = 1, reason: str = "理由の記載なし"):
    YOUR_USER_ID = 968461296334929973

    user_id = str(user.id)
    load_data() # まず最新状態を読み込む
    if str(user.id) not in profiles:
        profiles[str(user.id)] = {"report_count": 0, "report_reasons": []}
# その後、profiles を直接操作する
    
    if "report_reasons" not in data[user_id]:
        data[user_id]["report_reasons"] = []

    data[user_id]["report_count"] = data[user_id].get("report_count", 0) + points
    count = data[user_id]["report_count"]
    
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    data[user_id]["report_reasons"].append(f"[{now_str}] +{points}点: {reason}")
    save_data()
    

    role_warn1 = discord.utils.get(it.guild.roles, name="通報1回")
    role_warn2 = discord.utils.get(it.guild.roles, name="通報2回")
    role_warn3 = discord.utils.get(it.guild.roles, name="通報3回")
    role_warn4 = discord.utils.get(it.guild.roles, name="通報4回")

    roles_to_remove = [r for r in [role_warn1, role_warn2, role_warn3, role_warn4] if r and r in user.roles]
    if roles_to_remove:
        await user.remove_roles(*roles_to_remove)

    user_dm_sent = False
    try:
        user_embed = discord.Embed(
            title="⚠️ 【警告】サーバー運営からの通知", 
            description=f"あなたが所属しているサーバー「**{it.guild.name}**」にて、規約違反行為を確認したため、違反ポイントが付与されました。", 
            color=0xffcc00
        )
        user_embed.add_field(name="今回付与されたポイント", value=f"**+{points} ポイント**", inline=True)
        user_embed.add_field(name="現在のあなたの累計ポイント", value=f"**{count} / 5 ポイント**", inline=True)
        user_embed.add_field(name="📜 違反の理由", value=reason, inline=False)
        user_embed.add_field(
            name="📢 異議申し立てについて", 
            value="「身に覚えがない」など、処罰に対して反論がある場合は、**下のボタンを押して運営に申し立てを行うことができます。**"
        )
        user_embed.set_footer(text="※累計5ポイントに達すると、管理者による最終判決（BAN等）が行われます。")
        
        obj_view = ObjectionView()
        await user.send(embed=user_embed, view=obj_view)
        user_dm_sent = True
    except discord.Forbidden:
        print(f"⚠️ {user.name} への警告DM送信に失敗しました（DMが閉じられています）")
    except Exception as e:
        print(f"⚠️ ユーザーへのDM送信エラー: {e}")

    if count >= 5:
        history_text = "\n".join(data[user_id]["report_reasons"])
        dm_sent = False
        try:
            my_user = await bot.fetch_user(YOUR_USER_ID)
            if my_user:
                dm_embed = discord.Embed(title="⚖️ 【判決要請】ユーザーが5点に達しました", color=0xff0000)
                dm_embed.add_field(name="サーバー名", value=it.guild.name, inline=True)
                dm_embed.add_field(name="被告人（対象者）", value=f"{user.mention}\n{user.name} ({user.id})", inline=True)
                dm_embed.add_field(name="📜 罪の内容（これまでのすべての違反履歴）", value=history_text, inline=False)
                dm_embed.set_footer(text="内容を確認し、下のボタンを押して判決を下してください。")
                
                judge_view = JudgementView(guild_id=it.guild.id, target_user_id=user.id, reason=reason)
                await my_user.send(embed=dm_embed, view=judge_view)
                dm_sent = True
        except Exception as e:
            print(f"あなたへのDM送信に失敗しました: {e}")

        warn_embed = discord.Embed(title="⚖️ 審議入り（累計5点到達）", description=f"{user.mention} が累計 **{count}点** に達したため、サーバー主へ判決を委ねました。", color=0x000000)
        warn_embed.add_field(name="今回の理由", value=reason, inline=False)
        
        footer_text = "👑 サーバー主のDMにこれまでの罪の履歴と判決ボタンを送信しました。"
        if not user_dm_sent:
            footer_text += "\n⚠️ 対象ユーザーがDMを閉じているため、本人への警告通知は届きませんでした。"
        warn_embed.set_footer(text=footer_text)
        await it.response.send_message(embed=warn_embed)

    else:
        target_role = None
        color_text = ""
        if count == 1:
            target_role = role_warn1
            color_text = "🟡 名前を【黄色（1点）】に変更しました。"
        elif count == 2:
            target_role = role_warn2
            color_text = "🟠 名前を【オレンジ（2点）】に変更しました。"
        elif count == 3:
            target_role = role_warn3
            color_text = "🔴 名前を【赤色（3点）】に変更しました。"
        elif count == 4:
            target_role = role_warn4
            color_text = "⚫ 名前を【黒色（4点）】に変更しました。（あと1点で審議入り！）"

        if target_role:
            try:
                await user.add_roles(target_role)
            except Exception as e:
                color_text += f"\n*(⚠️ ロール付与エラー: Botの権限順位を確認してください)*"

        warn_embed = discord.Embed(title="⚠️ 違反ポイントの記録", description=f"{user.mention} に **{points}ポイント** を付与しました。\n{color_text}", color=0xf1c40f)
        warn_embed.add_field(name="今回の理由", value=reason, inline=False)
        warn_embed.add_field(name="現在の累計ポイント", value=f"**{count} / 5 ポイント**")
        
        footer_text = "累計5ポイントに達すると、管理者に履歴がDMされ、最終判決へ進みます。"
        if not user_dm_sent:
            footer_text += "\n⚠️ 対象ユーザーがDMを閉じているため、本人への警告通知は届きませんでした。"
        warn_embed.set_footer(text=footer_text)
        await it.response.send_message(embed=warn_embed)

@bot.tree.command(name="report_reset", description="管理用：指定したユーザーの違反ポイントと通報ロールを完全にリセットします")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    user="リセットするメンバー",
    reason="リセットする理由（例：反省したため、テスト終了のため）"
)
async def report_reset(it: discord.Interaction, user: discord.Member, reason: str = "理由の記載なし"):
    user_id = str(user.id)
    data = load_data()
    
    if user_id in data:
        data[user_id]["report_count"] = 0
        data[user_id]["report_reasons"] = []
        save_data()
        

    role_warn1 = discord.utils.get(it.guild.roles, name="通報1回")
    role_warn2 = discord.utils.get(it.guild.roles, name="通報2回")
    role_warn3 = discord.utils.get(it.guild.roles, name="通報3回")
    role_warn4 = discord.utils.get(it.guild.roles, name="通報4回")

    roles_to_remove = [r for r in [role_warn1, role_warn2, role_warn3, role_warn4] if r and r in user.roles]
    if roles_to_remove:
        try:
            await user.remove_roles(*roles_to_remove)
        except Exception as e:
            print(f"ロールの剥奪に失敗しました: {e}")

    reset_embed = discord.Embed(
        title="✨ 違反ポイントの完全リセット", 
        description=f"{user.mention} の違反ポイントおよび通報ロールをすべてリセットしました。名前の色が元に戻ります。", 
        color=0x2ecc71
    )
    reset_embed.add_field(name="理由", value=reason, inline=False)
    reset_embed.set_footer(text="現在の累計ポイント: 0 / 5 ポイント")
    
    await it.response.send_message(embed=reset_embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_agree(ctx):
    # ここに規約のメッセージを書く
    embed = discord.Embed(
        title="📋 サーバー利用規約",
        description="本サーバーのルールに同意される方は、以下のボタンを押して参加してください。",
        color=discord.Color.blue()
    )
    # ボタンと一緒に送信
    await ctx.send(embed=embed, view=AgreeView())

@bot.tree.command(name="kick_user", description="管理用：指定したユーザーを追放します")
@app_commands.checks.has_permissions(administrator=True)
async def kick_user(it: discord.Interaction, user: discord.Member, reason: str = "管理者による判断"):
    try:
        await user.kick(reason=reason)
        await it.response.send_message(f"👤 {user.mention} を追放しました。理由: {reason}")
    except:
        await it.response.send_message("❌ 権限が足りないため追放できませんでした。", ephemeral=True)

# 👇 起動ログ表示
print("★profiles.jsonの保存場所はここです：", os.path.abspath(DATA_FILE))


if __name__ == "__main__":
    # クラスをインスタンス化
    # 起動
    bot.run(TOKEN)
