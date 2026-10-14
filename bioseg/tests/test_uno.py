"""The Uno only has two bin commands. Hold shuts the gate and is not a bin."""

from bioseg.edge.uno import BIN_COMMANDS, command_for_flap


def test_two_bin_commands():
    assert command_for_flap("left") == "LEFT"
    assert command_for_flap("right") == "RIGHT"
    assert command_for_flap("centre") == "HOLD"
    assert command_for_flap("center") == "HOLD"
    assert command_for_flap("mystery") == "HOLD"
    assert BIN_COMMANDS == ("LEFT", "RIGHT")
    assert "HOLD" not in BIN_COMMANDS
