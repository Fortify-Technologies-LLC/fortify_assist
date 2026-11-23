# Security policy

Fortify Assist runs inside your Home Assistant and can operate devices, including locks, garage doors and alarms.
We treat anything that could make it act without the user asking, act on the wrong device, skip a confirmation,
or send data somewhere the user did not choose as a security issue.

## Reporting a vulnerability

Please report privately, not in a public issue: on GitHub open the **Security** tab of this repository and choose
**Report a vulnerability**. Include the Home Assistant version, the Fortify Assist version, what you said or sent,
and what happened. Remove personal details (names, addresses, device names) from diagnostics before attaching them.

We aim to acknowledge a report within a few days and will credit you in the changelog unless you would rather not.

## Supported versions

Only the latest release receives fixes.

## What the assistant is designed to guarantee

- It only sees and controls entities you have exposed to Assist.
- Every action is checked against your live home before it runs: the device must exist, be exposed and available,
  the service must belong to the device, and every value must be in range.
- Unlocking, disarming an alarm and opening garage doors, gates and doors always ask for confirmation first.
- A language model, if you configure one, never chooses a device or a service on its own: its output is checked
  against the words you actually said and the same validator as everything else.
- Text from news feeds and web pages is only ever read out; it never reaches the command parser.
