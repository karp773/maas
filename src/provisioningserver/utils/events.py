# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Event-related utilities."""

__all__ = [
    "Event",
    "EventGroup",
    ]


class Event:
    """Simple event that when fired calls all of the registered handlers on
    this event."""

    def __init__(self):
        self.handlers = set()

    def registerHandler(self, handler):
        """Register handler to event."""
        self.handlers.add(handler)

    def unregisterHandler(self, handler):
        """Unregister handler from event."""
        if handler in self.handlers:
            self.handlers.remove(handler)

    def fire(self, *args, **kwargs):
        """Fire the event."""
        for handler in self.handlers:
            handler(*args, **kwargs)


class EventGroup:
    """Group of events.

    Provides a quick way of creating a group of events for an object. Access
    the events as properties on this object.

    Example:
        events = EventGroup("connected", "disconnected")
        events.connected.fire()
        events.disconnected.fire()
    """

    def __init__(self, *events):
        for event in events:
            setattr(self, event, Event())