from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class AlertMessage:
    channel: str
    body: str


class InMemoryAlertSink:
    def __init__(self) -> None:
        self.messages: list[AlertMessage] = []

    def send(self, channel: str, body: str) -> None:
        self.messages.append(AlertMessage(channel=channel, body=body))
