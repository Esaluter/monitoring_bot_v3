from __future__ import annotations

import logging
import unittest

from monitoring_bot_v3.channel import ChannelPublisher
from monitoring_bot_v3.telegram_bot import split_telegram_text


def run_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("Корутина неожиданно приостановилась")


class FakeBot:
    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail

    async def send_message(self, **kwargs):
        if self.fail:
            raise RuntimeError("offline")
        self.messages.append(kwargs)


class ChannelTests(unittest.TestCase):
    def test_unconfigured_channel_is_optional(self):
        publisher = ChannelPublisher("", logging.getLogger("channel-test"))
        self.assertFalse(run_immediate(publisher.publish("text", split=split_telegram_text)))

    def test_long_report_is_delivered_in_order_without_loss(self):
        publisher = ChannelPublisher("-100123", logging.getLogger("channel-test"))
        publisher.bot = FakeBot()
        text = "строка\n" * 1500
        self.assertTrue(run_immediate(publisher.publish(text, split=split_telegram_text)))
        self.assertGreater(len(publisher.bot.messages), 1)
        self.assertEqual(text, "".join(item["text"] for item in publisher.bot.messages))

    def test_telegram_failure_is_contained(self):
        publisher = ChannelPublisher("-100123", logging.getLogger("channel-test"))
        publisher.bot = FakeBot(fail=True)
        self.assertFalse(run_immediate(publisher.event("safe event", split=split_telegram_text)))


if __name__ == "__main__":
    unittest.main()
