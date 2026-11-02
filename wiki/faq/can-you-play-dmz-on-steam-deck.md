---
type: faq
title: "Can you play DMZ on Steam Deck?"
description: "Not on a stock Steam Deck, by one pre-launch report: RICOCHET, TPM and Secure Boot block SteamOS. Windows handhelds such as the ROG Ally X can meet the rules. Not tested for DMZ itself."
status: stable
brain_status: published
sensitivity: public
sources: [src-timesaver-steam-deck-2026-09-26, src-activision-pc-specs-2026-09-29, src-activision-tpm-secure-boot-2026-10-21]
aliases: ["Steam Deck DMZ", "MW4 Steam Deck", "Modern Warfare 4 on Steam Deck", "ROG Ally DMZ", "handheld PC DMZ", "Legion Go DMZ", "SteamOS DMZ", "Proton MW4"]
---
<!-- generated:start -->
_No generated text yet. The Brain fills this zone from confirmed claims; nobody edits it by hand._
<!-- generated:end -->

<!-- human:start -->
Not on a stock Steam Deck, as far as we can tell. A blog written on 26 Sep 2026, before launch, says the game will not start on SteamOS: the RICOCHET anti-cheat driver does not run under Proton or Wine, and the Deck lacks the Windows checks behind TPM 2.0 and Secure Boot (reported) [[src-timesaver-steam-deck-2026-09-26]]. That source comes from a boosting service and does not discuss DMZ by name, so read this as the likely answer for the whole game, not a test of DMZ. We found no official statement about the Steam Deck.

## What is confirmed?
Activision requires TPM 2.0 and Secure Boot on PC [[src-activision-pc-specs-2026-09-29]] [[src-activision-tpm-secure-boot-2026-10-21]]. Details and setup: [[does-dmz-need-tpm-2-0-and-secure-boot]]. Anti-cheat: [[ricochet-anti-cheat]].

## What about Windows handhelds?
The same blog says the ROG Ally X, Legion Go and MSI Claw can meet those rules because they ship with Windows 11 (reported) [[src-timesaver-steam-deck-2026-09-26]]. We have no measured DMZ performance on them.

## Is there another handheld option?
The Switch 2 version is a real handheld route, though how DMZ runs away from the dock has not been covered; see [[what-platforms-is-dmz-on]]. If you try a handheld, tell us what you see, since this page rests on one source.
<!-- human:end -->
