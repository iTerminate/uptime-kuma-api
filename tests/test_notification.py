import unittest

import socketio

from uptime_kuma_api import UptimeKumaException, NotificationType
from uptime_kuma_test_case import UptimeKumaTestCase


class TestNotification(UptimeKumaTestCase):
    def test_notification(self):
        # get empty list to make sure that future accesses will also work
        self.api.get_notifications()

        expected_notification = {
            "name": "notification 1",
            "isDefault": True,
            "applyExisting": True,
            "type": NotificationType.TELEGRAM,
            "telegramChatID": "123456789",
            "telegramBotToken": "987654321",
            # required in Uptime Kuma 2.x
            "telegramTemplate": "{{name}} is {{status}}",
            "telegramTemplateParseMode": "plain",
        }

        # test notification — with bogus creds the server either responds
        # with an error (1.x: "Not Found") or hangs talking to the real
        # provider until the socket-call times out (2.x). Either way means
        # the test send didn't silently succeed.
        with self.assertRaises((UptimeKumaException, socketio.exceptions.TimeoutError)):
            self.api.test_notification(**expected_notification)

        # add notification
        r = self.api.add_notification(**expected_notification)
        self.assertEqual(r["msg"], "Saved.")
        notification_id = r["id"]

        # `applyExisting=True` is an action flag — the server uses it to
        # propagate the notification onto existing monitors at add/edit time
        # but always persists `applyExisting=False`, so it shouldn't be part
        # of the round-trip comparison.
        expected_persisted = {k: v for k, v in expected_notification.items() if k != "applyExisting"}

        # get notification
        notification = self.api.get_notification(notification_id)
        self.compare(notification, expected_persisted)

        # get notifications
        notifications = self.api.get_notifications()
        self.assertTrue(type(notifications[0]["type"]) == NotificationType)
        notification = self.find_by_id(notifications, notification_id)
        self.assertTrue(type(notification["type"]) == NotificationType)
        self.assertIsNotNone(notification)
        self.compare(notification, expected_persisted)

        # edit notification
        expected_notification["name"] = "notification 1 new"
        expected_notification["default"] = False
        expected_notification["applyExisting"] = False
        expected_notification["type"] = NotificationType.PUSHDEER
        expected_notification["pushdeerKey"] = "987654321"
        del expected_notification["telegramChatID"]
        del expected_notification["telegramBotToken"]
        del expected_notification["telegramTemplate"]
        del expected_notification["telegramTemplateParseMode"]
        r = self.api.edit_notification(notification_id, **expected_notification)
        self.assertEqual(r["msg"], "Saved.")
        notification = self.api.get_notification(notification_id)
        self.compare(notification, expected_notification)
        self.assertIsNone(notification.get("pushAPIKey"))

        # delete notification
        r = self.api.delete_notification(notification_id)
        self.assertEqual(r["msg"], "successDeleted")
        with self.assertRaises(UptimeKumaException):
            self.api.delete_notification(notification_id)

    def test_delete_not_existing_notification(self):
        with self.assertRaises(UptimeKumaException):
            self.api.delete_notification(42)


if __name__ == '__main__':
    unittest.main()
