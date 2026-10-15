# SICHIRAY headset: what we found

This is a record of the physical headset and data stream investigated for this
project. The findings apply to this unit and the connection paths we tested; the exact internal chip
and firmware revision have not been identified.

**Main finding:** both the USB receiver and direct BLE exposed processed band-power
summaries at roughly one update per second. Neither tested path exposed the raw
EEG waveform. Connecting directly over BLE did not increase the update rate.

## Contents

1. [Physical device](#physical-device)
2. [Connection paths](#connection-paths)
3. [Direct BLE layout](#direct-ble-layout)
4. [What the packets contain](#what-the-packets-contain)
5. [Quality and proprietary scores](#quality-and-proprietary-scores)
6. [Raw-waveform command experiment](#raw-waveform-command-experiment)
7. [Battery information](#battery-information)
8. [Timing and missing data](#timing-and-missing-data)
9. [What the analysis can access](#what-the-analysis-can-access)
10. [Unresolved questions](#unresolved-questions)
11. [Evidence and references](#evidence-and-references)

## Physical device

The headset is a SICHIRAY Taurus-style single-channel device, with a forehead
contact, ear contacts and a wireless USB receiver. The forehead contact is the
measurement site; the ear contacts provide the reference/ground arrangement,
rather than two additional independent EEG channels. We did not independently
trace the internal electrode wiring.

The receiver appeared on macOS as a CH340 USB serial interface. Reading at
9600 baud produced a ThinkGear-compatible byte stream. The serial port name is
assigned by the computer and is not a permanent identifier for the model.

The ThinkGear-compatible packet format does **not** establish that the internal
hardware is a particular NeuroSky TGAM chip. No model or firmware identifier was
available through the GATT services discovered during our BLE inspection.

## Connection paths

We successfully collected data through two paths:

```text
headset -> paired wireless USB receiver -> CH340 serial interface -> computer
headset -> direct Bluetooth Low Energy connection -> computer
```

The first path was read at 9600 baud. The second used a BLE notification
subscription. Both delivered the same kind of aggregate fields: quality, eight
band powers, Attention and Meditation.

The public Python scripts in this repository start from decoded JSONL files.
They do not connect to the headset or send configuration commands. The collection
and diagnostic tools used for this investigation are in the original project.

## Direct BLE layout

The device advertised the name `Sichiray`. Its advertisements included the UUIDs
`FFE0` and `FEE7`, but the connected service list exposed `FFE0`. An advertised
UUID and a service actually discovered after connection are different observations.

| Item | Observed on this unit |
|---|---|
| Connected service | `FFE0` |
| `FFE1` | Notify, write, write without response |
| `FFE2` | Notify, write, write without response |
| `FFE3` | Write, write without response |
| Negotiated ATT MTU | 244 bytes |
| Characteristic that delivered data | `FFE1` |
| Notifications observed from `FFE2` after subscription | None |

These are shortened UUIDs. For example, `FFE1` expands to
`0000ffe1-0000-1000-8000-00805f9b34fb`.

The older SICHIRAY example repository describes `FFE1`, `FFF7` and `FFF8` on its
iOS path. Our unit instead exposed `FFE1`, `FFE2` and `FFE3`. We therefore could
not assume that its write characteristic or firmware behavior matched ours.
The negotiated MTU of 244 did not make the device send larger EEG packets: the
observed notifications were still 36 bytes each.

## What the packets contain

Each notification in the repeated BLE diagnostic captures contained one complete,
checksum-valid, 36-byte ThinkGear packet:

```text
AA AA 20 [32 bytes of payload] [1 checksum byte]
```

`AA AA` marks packet synchronization. `20` is hexadecimal for 32, the payload
length. The total is two synchronization bytes, one length byte, 32 payload
bytes and one checksum byte.

The observed payload layout was:

```text
02 [quality: 1 byte]
83 18 [eight band powers: 24 bytes]
04 [Attention: 1 byte]
05 [Meditation: 1 byte]
```

| Code | Field | Value bytes |
|---|---|---:|
| `0x02` | Poor-signal quality | 1 |
| `0x83` | Eight band powers | 24 |
| `0x04` | Attention | 1 |
| `0x05` | Meditation | 1 |

The byte `18` after `83` is hexadecimal for 24. Each band occupies three bytes,
decoded as an unsigned big-endian integer: the most significant byte comes first.
The checksum rule is that the low eight bits of `sum(payload) + checksum` equal
`0xFF`. We checked packet framing and checksums rather than treating every BLE
notification as valid EEG automatically.

The decoded band order, interpreted using the protocol reference, is:

1. Delta
2. Theta
3. Low alpha
4. High alpha
5. Low beta
6. High beta
7. Low gamma
8. Middle gamma

These are onboard summary values. We have not measured their exact filter
boundaries, processing windows, overlap or scaling. The protocol describes band
powers without physical units; the transmitted integers should not be presented
as calibrated microvolt-squared measurements.

The layout above describes our observed packets. The broader ThinkGear protocol
allows other DataRows and orders, so a general parser cannot assume fixed offsets.

## Quality and proprietary scores

The protocol documentation helped interpret the fields we observed:

| Field/value | Interpretation |
|---|---|
| Quality `0` | The device has not flagged poor signal |
| Quality `200` | The protocol's no-skin-contact indicator; also observed while our unit was unworn |
| Attention or Meditation `0` | An unavailable/unreliable estimate, not zero attention or relaxation |

We also encountered zero Attention/Meditation values while quality was zero.
Checking quality alone therefore does not make those scores valid.

Quality zero is not proof that a change came from cortical activity. We have no
independent artifact label for each change, and the quality byte does not expose
separate blink, eye-movement, muscle or movement measurements. The public detector
uses the eight bands and quality context, rather than the proprietary scores.

## Raw-waveform command experiment

The broader ThinkGear protocol defines a raw-waveform DataRow, `0x80`, containing
a signed two-byte sample. We looked for that DataRow in the packet structure;
finding a byte with value `80` inside a band number would not count as a raw sample.

On October 14, with the headset unworn, we tried the documented ASIC command
`0x02` through the write-only `FFE3` characteristic, then sent `0x00` as a restore
attempt. The protocol table describes these as normal-plus-raw output at 57,600
baud and normal output at 9600 baud respectively. That serial-ASIC table does not
prove that this BLE module forwards those commands to its EEG processor.

| Phase | Notifications received during that phase | Observation |
|---|---:|---|
| Before command | 3 | Summary packets only |
| Roughly eight-second raw-mode attempt | 8 | Summary packets only |
| After restore attempt | 3 | Summary packets only |

The diagnostic's counter was cumulative: it read 3, then 11, then 14. The middle
phase therefore received **8 additional notifications, not 11**. The intervals
remained around 1.05 seconds; there was no observed rate increase.

Both BLE writes completed, but there was no application-level acknowledgment
confirming that the EEG processor accepted or applied either command. No `0x80`
raw waveform appeared, and `FFE2` remained silent. Quality stayed at 200, so
changes in the reported bands during this unworn probe were not usable EEG.

Our conclusion is limited: **this command attempt did not unlock raw output on
the tested path**. It does not prove that raw output is impossible on every
SICHIRAY variant or connection method.

## Battery information

The physical cell label gives a nominal voltage of 3.7 V, capacity of 300 mAh and
energy of 1.11 Wh. The energy is consistent with `3.7 V * 0.3 Ah = 1.11 Wh`.
We did not establish operating runtime from the label.

No battery DataRow `0x01` was observed in the inspected packets. No standard BLE
Battery Service (`180F`) or Battery Level characteristic (`2A19`) appeared in the
connected service list. Although the broader protocol describes a battery field,
that does not establish support on this unit.

Packet silence alone cannot tell us whether the battery is empty: power-off,
connection loss and receiver problems can also stop the stream.

## Timing and missing data

The recording timestamps are computer-side packet-arrival times. They do not
identify when the internal spectral window started or ended. Radio transport,
receiver buffering and operating-system scheduling can add unknown latency.

`unix_ns` is the recording format's Unix timestamp in nanoseconds, not a device
sample counter. One second is 1,000,000,000 nanoseconds. Subtracting two such
timestamps gives elapsed nanoseconds; divide by that number to get seconds.

We have not established a device-side buffer or a way to recover packets missed
during a disconnect. The analysis leaves missing intervals missing. Its
five-second bins use midpoint timestamps, which can precede the last contributing
packet by about 2.5 seconds; those are display/aggregation times, not exact
physiological event times.

## What the analysis can access

| Available from the tested stream | Not available from the tested stream |
|---|---|
| Eight proprietary band-power summaries | Raw EEG waveform |
| Poor-signal quality byte | Waveform phase or within-band detail |
| Attention and Meditation fields | Independent spatial EEG channels |
| Computer-side packet-arrival timestamps | Exact internal processing-window timing |

The public code aggregates band summaries into five-second bins, using median
log-transformed powers from quality-zero samples. It then measures changes over
adjacent 20-second windows. Those choices are analysis settings, not the headset's
internal sampling or processing settings.

## Unresolved questions

- The exact EEG processor and firmware revision.
- The internal raw sampling rate and filtering pipeline.
- The exact spectral estimation windows, scaling and overlap.
- Whether `FFE3` forwards commands to the EEG processor, and what `FFE2` is for.
- Whether a different supported configuration can expose raw waveform or battery data.
- The end-to-end delay between a physiological change and its summary packet.
- Operating runtime, usable radio range and packet recovery behavior under tested conditions.

## Evidence and references

The local evidence consists of serial recordings, BLE service enumeration,
notification timing, checksum-validated packet decoding, and the command probe.
The packet captures remain private; this document contains device/transport
findings rather than personal recording traces or identifiers.

Two external references were used to interpret and compare what we found:

- [NeuroSky ThinkGear communications protocol](https://developer.neurosky.com/docs/doku.php?id=thinkgear_communications_protocol):
  packet framing, DataRow encoding, field semantics and the ASIC command table.
  Protocol capability is not proof that this device implements every field or command.
- [SICHIRAY BLE example repository at the inspected revision](https://github.com/selectgithub/NeuroSkyThinkGearBLE/tree/b1b09db0be1462e20f4edea287faf799916928e4):
  a compatible aggregate layout and example BLE characteristics. Its layout differs
  from the GATT layout observed on our unit.

We did not independently measure the frequency ranges or internal sampling rates
listed in product/protocol literature, so they are not reported here as discoveries.
