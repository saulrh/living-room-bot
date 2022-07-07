import click
import discord
import datetime

import apscheduler.jobstores.memory
import apscheduler.schedulers.asyncio
import apscheduler.schedulers.base
import apscheduler.triggers.interval
import apscheduler.triggers.date


class ChannelNotFoundError(Exception):
    pass


class _LivingRoomClient(discord.Client):
    def __init__(
        self,
        text_id: int,
        voice_id: int,
        gc_after: datetime.timedelta,
        gc_horizon: datetime.timedelta,
        sched: apscheduler.schedulers.base.BaseScheduler,
        debounce_period: datetime.timedelta,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._voice_id = voice_id
        self._text_id = text_id
        self._gc_after = gc_after
        self._gc_horizon = gc_horizon
        self._sched = sched
        self._debounce_period = debounce_period

    @property
    def text_channel(self) -> discord.TextChannel:
        c = self.get_channel(self._text_id)
        if not c:
            raise ChannelNotFoundError(f"Did not find channel with id {self._text_id}")
        return c

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if after.channel:
            user_changed_channels = after.channel != before.channel
            only_person_in_channel = len(after.channel.members) == 1
            in_living_room = after.channel.id == self._voice_id
            if user_changed_channels and only_person_in_channel and in_living_room:
                bounce_check_time = (
                    datetime.datetime.now(tz=datetime.timezone.utc)
                    + self._debounce_period
                )
                self._sched.add_job(
                    func=self.check_debounce,
                    trigger=apscheduler.triggers.date.DateTrigger(bounce_check_time),
                    args=[member, after.channel],
                )

    async def clean_up_old_notifications(self):
        start_time = (
            datetime.datetime.now(tz=datetime.timezone.utc) - self._gc_horizon
        ).replace(tzinfo=None)
        gc_horizon = (
            datetime.datetime.now(tz=datetime.timezone.utc) - self._gc_after
        ).replace(tzinfo=None)
        async for message in self.text_channel.history(limit=1000, after=start_time):
            if message.author == self.user and message.created_at < gc_horizon:
                await message.delete()

    async def check_debounce(
        self, member: discord.Member, channel: discord.VoiceChannel
    ):
        if member in channel.members:
            await self.text_channel.send(f"Someone's in the living room!")


_CLIENT = None


def get_client(*args, **kwargs) -> _LivingRoomClient:
    global _CLIENT
    if not _CLIENT:
        _CLIENT = _LivingRoomClient(
            intents=discord.Intents(voice_states=True, guilds=True),
            *args,
            **kwargs,
        )
    return _CLIENT


def make_scheduler(*args, **kwargs):
    jobstores = {
        "default": apscheduler.jobstores.memory.MemoryJobStore(*args, **kwargs)
    }
    scheduler = apscheduler.schedulers.asyncio.AsyncIOScheduler(jobstores=jobstores)
    scheduler.start()
    return scheduler


@click.command()
@click.option(
    "--discord_bot_token",
    envvar="DISCORD_BOT_TOKEN",
    type=str,
    required=True,
    help="Discord bot token",
)
@click.option(
    "--voice_id",
    envvar="VOICE_CHANNEL_ID",
    type=int,
    required=True,
    help="Int ID of the voice channel to monitor for joins/parts",
)
@click.option(
    "--text_id",
    envvar="TEXT_CHANNEL_ID",
    type=int,
    required=True,
    help="Int ID of the text channel to post notifications in",
)
@click.option(
    "--message_gc_frequency",
    envvar="MESSAGE_GC_FREQUENCY",
    type=int,
    default=60 * 10,
    help="Int seconds between checks for old notifications to garbage collect",
)
@click.option(
    "--message_gc_horizon",
    envvar="MESSAGE_GC_HORIZON",
    type=int,
    default=60 * 60 * 24,
    help="Int seconds to look back for old notifications to garbage collect",
)
@click.option(
    "--message_gc_after",
    envvar="MESSAGE_GC_AFTER",
    type=int,
    default=60 * 60,
    help="Int seconds beyond which old notifications will be garbage collected",
)
@click.option(
    "--debounce_period",
    envvar="DEBOUNCE_PERIOD",
    type=int,
    default=10,
    help="Only register joins if the user stays in the channel for this many seconds",
)
def run(
    discord_bot_token: str,
    voice_id: int,
    text_id: int,
    message_gc_frequency: int,
    message_gc_horizon: int,
    message_gc_after: int,
    debounce_period: int,
):
    sched = make_scheduler()
    client = get_client(
        text_id=text_id,
        voice_id=voice_id,
        gc_after=datetime.timedelta(seconds=message_gc_after),
        gc_horizon=datetime.timedelta(seconds=message_gc_horizon),
        sched=sched,
        debounce_period=datetime.timedelta(seconds=debounce_period),
    )
    sched.add_job(
        func=client.clean_up_old_notifications,
        trigger=apscheduler.triggers.interval.IntervalTrigger(
            seconds=message_gc_frequency,
        ),
        args=[],
        id="periodic_garbage_collect",
    )
    client.run(discord_bot_token)


if __name__ == "__main__":
    run()
