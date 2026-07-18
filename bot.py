"""
特戰英豪組隊機器人 (Valorant Room-Based Team-Up Bot)
使用 discord.py + slash commands + 按鈕互動訊息框

功能:
  /面板      發布組隊面板(附「創建房間」「房間列表」按鈕),之後大家不用打指令,點按鈕就好
  /開房      用指令方式建立房間(不想用面板的話可以用這個)
  /房間列表  查看目前所有開放中的房間
  /解散房間  房主或管理員可手動解散指定房號的房間
  /統計      查看自己或指定對象的組隊完成次數

特色:
  - 點「創建房間」先選段位(官方 9 大階級、鐵牌~神話各分 1-3 區間,輻能戰魂無區間),
    再跳出輸入視窗填:需要人數、遊戲房號、還缺的位置
  - 房間卡片有「加入房間」「離開房間」按鈕,不用打指令
  - 人數湊滿時,自動建立臨時語音頻道,所有人離開後自動刪除該頻道
  - 房間超過 30 分鐘沒滿會自動解散,避免面板堆滿舊房間
  - 每次組隊成功,參與者的組隊次數會被記錄,可用 /統計 查詢

⚠️ 需要在 Discord 開發者後台的邀請權限中額外勾選「管理頻道」(Manage Channels),
   否則機器人只能發文字組隊通知,無法自動建立語音頻道。
"""

import os
import random
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

ROOM_TIMEOUT_MINUTES = 30  # 房間逾時自動解散的時間(分鐘)

# 《特戰英豪》官方段位:前 8 個階級各分 1-3 區間,最高階「輻能戰魂」無區間
RANK_TIERS = ["鐵牌", "銅牌", "銀牌", "金牌", "白金", "鑽石", "超凡入聖", "神話"]
RANK_OPTIONS: list[str] = [f"{tier} {i}" for tier in RANK_TIERS for i in (1, 2, 3)]
RANK_OPTIONS.append("輻能戰魂")  # 共 25 個選項,剛好符合 Discord 選單上限

intents = discord.Intents.default()
intents.voice_states = True  # 需要這個才能偵測語音頻道何時變空
bot = commands.Bot(command_prefix="!", intents=intents)

# 機器人自動建立的臨時語音頻道 id 集合,空了就自動刪除
auto_voice_channels: set[int] = set()

# 組隊完成次數統計: {user_id: 完成場數}
team_stats: dict[int, int] = {}


class Room:
    def __init__(
        self,
        code: str,
        host_id: int,
        team_size: int,
        guild_id: int,
        game_code: str = None,
        rank: str = None,
        positions: str = None,
    ):
        self.code = code
        self.host_id = host_id
        self.team_size = team_size
        self.members: list[int] = [host_id]
        self.guild_id = guild_id
        self.game_code = game_code       # 特戰英豪遊戲內的房間邀請碼
        self.rank = rank                 # 想找的段位
        self.positions = positions       # 還缺的位置需求
        self.closed = False
        self.created_at = datetime.now(timezone.utc)
        self.message: discord.Message = None  # 之後用來逾時編輯訊息


# 房號 -> Room
rooms: dict[str, Room] = {}


def generate_room_code() -> str:
    while True:
        code = str(random.randint(1000, 9999))
        if code not in rooms:
            return code


def build_room_embed(room: Room) -> discord.Embed:
    members_text = "\n".join(
        f"{i+1}. <@{uid}>" for i, uid in enumerate(room.members)
    ) or "尚無成員"
    game_code_text = f"`{room.game_code}`" if room.game_code else "房主尚未提供"
    rank_text = room.rank if room.rank else "不限"
    positions_text = room.positions if room.positions else "不限"

    embed = discord.Embed(
        title=f"🎮 房間 #{room.code}",
        description=(
            f"房主:<@{room.host_id}>\n"
            f"🎯 遊戲房號:{game_code_text}\n"
            f"🏅 段位需求:{rank_text}\n"
            f"🧩 還缺位置:{positions_text}\n"
            f"人數:{len(room.members)}/{room.team_size}\n\n"
            f"成員:\n{members_text}"
        ),
        color=discord.Color.blue(),
    )
    embed.set_footer(text=f"點下方按鈕加入或離開這個房間・{ROOM_TIMEOUT_MINUTES} 分鐘沒滿會自動解散")
    return embed


async def create_room_voice_channel(guild: discord.Guild, text_channel, room: Room):
    """組隊人數滿了,自動建立臨時語音頻道;失敗回傳 None(不影響文字組隊通知)。"""
    category = getattr(text_channel, "category", None)
    channel_name = f"🎮 房間-{room.code}"
    try:
        voice_channel = await guild.create_voice_channel(
            name=channel_name,
            category=category,
            reason="組隊房間人數已滿,自動建立語音頻道",
        )
        auto_voice_channels.add(voice_channel.id)
        return voice_channel
    except (discord.Forbidden, discord.HTTPException):
        return None


def record_team_completed(member_ids: list[int]):
    for uid in member_ids:
        team_stats[uid] = team_stats.get(uid, 0) + 1


class CreateRoomModal(discord.ui.Modal, title="建立組隊房間"):
    team_size_input = discord.ui.TextInput(
        label="需要幾個人?(2-10)",
        placeholder="例如:5",
        max_length=2,
        required=True,
    )
    game_code_input = discord.ui.TextInput(
        label="特戰英豪遊戲房號(選填)",
        placeholder="例如:ABC123,填了隊友才知道要打哪個房號",
        required=False,
        max_length=30,
    )
    positions_input = discord.ui.TextInput(
        label="還缺的位置(選填)",
        placeholder="例如:controller、duelist、sentinel",
        required=False,
        max_length=50,
    )

    def __init__(self, rank: str = None):
        super().__init__()
        self.rank = rank

    async def on_submit(self, interaction: discord.Interaction):
        raw_size = self.team_size_input.value.strip()
        if not raw_size.isdigit() or not (2 <= int(raw_size) <= 10):
            await interaction.response.send_message(
                "人數請輸入 2 到 10 之間的數字。", ephemeral=True
            )
            return
        team_size = int(raw_size)
        game_code = self.game_code_input.value.strip() or None
        positions = self.positions_input.value.strip() or None

        internal_code = generate_room_code()
        room = Room(
            code=internal_code,
            host_id=interaction.user.id,
            team_size=team_size,
            guild_id=interaction.guild_id,
            game_code=game_code,
            rank=self.rank,
            positions=positions,
        )
        rooms[internal_code] = room

        view = RoomView(internal_code)
        await interaction.response.send_message(embed=build_room_embed(room), view=view)
        room.message = await interaction.original_response()


class RankSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=rank) for rank in RANK_OPTIONS]
        super().__init__(placeholder="選擇這場想找的段位", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        chosen_rank = self.values[0]
        await interaction.response.send_modal(CreateRoomModal(rank=chosen_rank))


class RankSelectView(discord.ui.View):
    """建房前先選段位的畫面,也提供「不限段位」按鈕可略過選擇。"""

    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(RankSelect())

    @discord.ui.button(label="不限段位", style=discord.ButtonStyle.secondary)
    async def no_rank_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CreateRoomModal(rank=None))


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🆕 創建房間", style=discord.ButtonStyle.success, custom_id="panel_create_room")
    async def create_room_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "先選這場想找的段位(或選「不限段位」略過):",
            view=RankSelectView(),
            ephemeral=True,
        )

    @discord.ui.button(label="📋 房間列表", style=discord.ButtonStyle.primary, custom_id="panel_list_rooms")
    async def list_rooms_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_rooms = [r for r in rooms.values() if r.guild_id == interaction.guild_id and not r.closed]

        if not guild_rooms:
            await interaction.response.send_message(
                "目前沒有任何開放中的房間,點「創建房間」開一個吧!", ephemeral=True
            )
            return

        embed = discord.Embed(title="📋 目前開放中的房間", color=discord.Color.gold())
        for room in guild_rooms:
            game_code_text = f"`{room.game_code}`" if room.game_code else "尚未提供"
            rank_text = room.rank if room.rank else "不限"
            positions_text = room.positions if room.positions else "不限"
            embed.add_field(
                name=f"房號 #{room.code}",
                value=(
                    f"房主:<@{room.host_id}>\n"
                    f"遊戲房號:{game_code_text}\n"
                    f"段位:{rank_text}\n"
                    f"缺位置:{positions_text}\n"
                    f"人數:{len(room.members)}/{room.team_size}"
                ),
                inline=True,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


def build_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎮 特戰英豪組隊面板",
        description=(
            "點下方按鈕操作,不用打指令:\n\n"
            "🆕 **創建房間** — 先選段位,再設定人數、遊戲房號、缺的位置,開一個新房間\n"
            "📋 **房間列表** — 查看目前所有開放中的房間\n\n"
            f"房間如果 {ROOM_TIMEOUT_MINUTES} 分鐘內沒湊滿,會自動解散喔"
        ),
        color=discord.Color.dark_red(),
    )
    return embed


class RoomView(discord.ui.View):
    def __init__(self, room_code: str):
        super().__init__(timeout=None)
        self.room_code = room_code

    @discord.ui.button(label="加入房間", style=discord.ButtonStyle.success, custom_id="room_join")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        room = rooms.get(self.room_code)
        if room is None or room.closed:
            await interaction.response.send_message("這個房間已經關閉了。", ephemeral=True)
            return
        if interaction.user.id in room.members:
            await interaction.response.send_message("你已經在這個房間裡了。", ephemeral=True)
            return
        if len(room.members) >= room.team_size:
            await interaction.response.send_message("房間已滿了。", ephemeral=True)
            return

        room.members.append(interaction.user.id)

        if len(room.members) >= room.team_size:
            # 人數湊滿,組隊出發
            room.closed = True
            voice_channel = await create_room_voice_channel(
                interaction.guild, interaction.channel, room
            )
            record_team_completed(room.members)

            mentions = " ".join(f"<@{uid}>" for uid in room.members)
            description = f"{mentions}\n\n人到齊了,準備開局吧!祝各位 ACE 連發 🔥"
            if voice_channel:
                description += f"\n\n🔊 語音頻道已建好:{voice_channel.mention}"

            full_embed = discord.Embed(
                title=f"✅ 房間 #{room.code} 已滿!",
                description=description,
                color=discord.Color.red(),
            )
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(embed=full_embed, view=self)
            rooms.pop(self.room_code, None)
        else:
            await interaction.response.edit_message(embed=build_room_embed(room), view=self)

    @discord.ui.button(label="離開房間", style=discord.ButtonStyle.danger, custom_id="room_leave")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        room = rooms.get(self.room_code)
        if room is None or room.closed:
            await interaction.response.send_message("這個房間已經關閉了。", ephemeral=True)
            return
        if interaction.user.id not in room.members:
            await interaction.response.send_message("你不在這個房間裡。", ephemeral=True)
            return

        if interaction.user.id == room.host_id:
            # 房主離開,直接解散房間
            room.closed = True
            rooms.pop(self.room_code, None)
            for child in self.children:
                child.disabled = True
            embed = discord.Embed(
                title=f"🚪 房間 #{room.code} 已解散",
                description="房主已離開,房間自動解散。",
                color=discord.Color.greyple(),
            )
            await interaction.response.edit_message(embed=embed, view=self)
            return

        room.members.remove(interaction.user.id)
        await interaction.response.edit_message(embed=build_room_embed(room), view=self)


@tasks.loop(minutes=1)
async def check_expired_rooms():
    now = datetime.now(timezone.utc)
    expired_codes = [
        code for code, room in rooms.items()
        if not room.closed and now - room.created_at > timedelta(minutes=ROOM_TIMEOUT_MINUTES)
    ]

    for code in expired_codes:
        room = rooms.pop(code, None)
        if room is None:
            continue
        room.closed = True
        if room.message is not None:
            try:
                embed = discord.Embed(
                    title=f"⌛ 房間 #{room.code} 已逾時解散",
                    description=f"超過 {ROOM_TIMEOUT_MINUTES} 分鐘沒有湊滿人數,房間自動關閉。",
                    color=discord.Color.greyple(),
                )
                disabled_view = RoomView(code)
                for child in disabled_view.children:
                    child.disabled = True
                await room.message.edit(embed=embed, view=disabled_view)
            except discord.HTTPException:
                pass


@bot.event
async def on_ready():
    bot.add_view(PanelView())  # 讓面板按鈕在機器人重啟後依然能用
    if not check_expired_rooms.is_running():
        check_expired_rooms.start()
    try:
        synced = await bot.tree.sync()
        print(f"已同步 {len(synced)} 個 slash 指令")
    except Exception as e:
        print(f"指令同步失敗: {e}")
    print(f"{bot.user} 已上線,準備組隊!")


@bot.event
async def on_voice_state_update(member, before, after):
    # 如果有人離開了機器人自動建立的臨時語音頻道,檢查是否已經空了
    if before.channel and before.channel.id in auto_voice_channels:
        if len(before.channel.members) == 0:
            auto_voice_channels.discard(before.channel.id)
            try:
                await before.channel.delete(reason="組隊語音頻道已空,自動清理")
            except discord.HTTPException:
                pass


@bot.tree.command(name="面板", description="發布組隊面板(附創建房間、房間列表按鈕),之後大家點按鈕操作就好")
async def post_panel(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_panel_embed(), view=PanelView())


@app_commands.choices(
    段位=[app_commands.Choice(name=rank, value=rank) for rank in RANK_OPTIONS]
)
@bot.tree.command(name="開房", description="建立一個新的組隊房間")
@app_commands.describe(
    人數="這個房間需要幾個人(2-10)",
    遊戲房號="你在特戰英豪遊戲裡的房間邀請碼(選填)",
    段位="這場想找的段位(選填)",
    缺的位置="還缺什麼位置,例如 controller、duelist(選填)",
)
async def create_room(
    interaction: discord.Interaction,
    人數: app_commands.Range[int, 2, 10],
    遊戲房號: str = None,
    段位: app_commands.Choice[str] = None,
    缺的位置: str = None,
):
    internal_code = generate_room_code()
    room = Room(
        code=internal_code,
        host_id=interaction.user.id,
        team_size=人數,
        guild_id=interaction.guild_id,
        game_code=遊戲房號,
        rank=段位.value if 段位 else None,
        positions=缺的位置,
    )
    rooms[internal_code] = room

    view = RoomView(internal_code)
    await interaction.response.send_message(embed=build_room_embed(room), view=view)
    room.message = await interaction.original_response()


@bot.tree.command(name="房間列表", description="查看目前所有開放中的房間")
async def list_rooms(interaction: discord.Interaction):
    guild_rooms = [r for r in rooms.values() if r.guild_id == interaction.guild_id and not r.closed]

    if not guild_rooms:
        await interaction.response.send_message("目前沒有任何開放中的房間,用 /開房 開一個吧!")
        return

    embed = discord.Embed(title="📋 目前開放中的房間", color=discord.Color.gold())
    for room in guild_rooms:
        game_code_text = f"`{room.game_code}`" if room.game_code else "尚未提供"
        rank_text = room.rank if room.rank else "不限"
        positions_text = room.positions if room.positions else "不限"
        embed.add_field(
            name=f"房號 #{room.code}",
            value=(
                f"房主:<@{room.host_id}>\n"
                f"遊戲房號:{game_code_text}\n"
                f"段位:{rank_text}\n"
                f"缺位置:{positions_text}\n"
                f"人數:{len(room.members)}/{room.team_size}"
            ),
            inline=True,
        )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="解散房間", description="解散指定房號的房間(房主或管理員可用)")
@app_commands.describe(房號="要解散的房號")
async def close_room(interaction: discord.Interaction, 房號: str):
    room = rooms.get(房號)
    if room is None:
        await interaction.response.send_message("找不到這個房號,可能已經解散或組隊完成了。", ephemeral=True)
        return

    is_host = interaction.user.id == room.host_id
    is_admin = interaction.user.guild_permissions.manage_guild
    if not (is_host or is_admin):
        await interaction.response.send_message(
            "只有房主或擁有「管理伺服器」權限的人可以解散這個房間。", ephemeral=True
        )
        return

    room.closed = True
    rooms.pop(房號, None)
    await interaction.response.send_message(f"🧹 房間 #{房號} 已解散。")


@bot.tree.command(name="統計", description="查看自己或指定對象的組隊完成次數")
@app_commands.describe(對象="要查詢的人(不填則查詢自己)")
async def show_stats(interaction: discord.Interaction, 對象: discord.Member = None):
    target = 對象 or interaction.user
    count = team_stats.get(target.id, 0)
    await interaction.response.send_message(
        f"📊 <@{target.id}> 目前已完成 **{count}** 場組隊。"
    )


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError(
            "找不到 DISCORD_TOKEN,請在 .env 檔案中設定你的 Discord bot token。"
        )
    bot.run(TOKEN)
