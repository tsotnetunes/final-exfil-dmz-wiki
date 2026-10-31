---
type: mechanic
title: "RICOCHET Anti-Cheat"
description: "How cheating is fought in DMZ: TPM 2.0 and Secure Boot on PC, Server Culling, DMA cheat detection from 23 Oct 2026, hardware bans carried into MW4, and what Activision's policy punishes."
status: stable
brain_status: published
sensitivity: public
sources: [src-activision-tpm-secure-boot-2026-10-21, src-activision-security-enforcement-policy-2026-07-31, src-activision-pc-specs-2026-09-29, src-shanethegamer-ricochet-bans-2026-09-29, src-gosugamers-ricochet-bans-2026-10-31, src-gameriv-ricochet-dma-2026-10-24, src-dexerto-server-culling-2026-10-02, src-westie-dmz-ama-recap-2026-10-11]
aliases: ["Ricochet", "anti-cheat", "anticheat", "cheating", "cheaters", "hackers", "hacking", "bans", "ban", "hardware ban", "DMA Detection", "DMA cheats", "Stronger Hardware Bans", "TPM 2.0", "Secure Boot", "attestation", "Failed Attestation Status", "Secure Attestation Wizard", "Cronus", "XIM"]
---
<!-- generated:start -->
_No generated text yet. The Brain fills this zone from confirmed claims; nobody edits it by hand._
<!-- generated:end -->

<!-- human:start -->
RICOCHET is Call of Duty's anti-cheat system, and it guards DMZ like every other Modern Warfare 4 mode: checks on your PC before you can play online, detection on the servers, and bans that follow the hardware as well as the account [[src-activision-pc-specs-2026-09-29]] [[src-shanethegamer-ricochet-bans-2026-09-29]]. Infinity Ward confirmed on 30 Oct 2026 that Server Culling has run in DMZ since day one [clm_16b6ee610da3], and other accounts report that the wider protections have been active in Multiplayer and DMZ since launch (reported) [clm_cd65cdd88e65].

## What does RICOCHET use in MW4?

| Layer | What it does | Since | Sources |
| --- | --- | --- | --- |
| TPM 2.0 and Secure Boot (PC) | Makes sure only trusted software starts with Windows; without them a PC cannot play any online mode of MW4 | DMZ early access, 20 Oct 2026 | [[src-activision-tpm-secure-boot-2026-10-21]] [clm_e201b18bb17c] |
| [[server-culling]] | Keeps hidden enemies' positions off your machine, so wallhacks show nothing | Launch | [[src-dexerto-server-culling-2026-10-02]] [clm_16b6ee610da3] |
| DMA detection | New detections against DMA cheats, which use a separate piece of hardware to read game memory and show things such as enemy positions | 23 Oct 2026 | [clm_dee5007d4538] [clm_fed37aaa79dd] [[src-gameriv-ricochet-dma-2026-10-24]] |
| Hardware bans | A ban on the machine itself, so a new account on the same PC or console gets nowhere; bans issued before launch carried into MW4 | Before launch | [[src-shanethegamer-ricochet-bans-2026-09-29]] [[src-gosugamers-ricochet-bans-2026-10-31]] |
| Input devices | Accounts using Cronus Zen, XIM Matrix and similar gear to cheat are judged by how they play, and banned | Before launch | [[src-shanethegamer-ricochet-bans-2026-09-29]] [[src-gosugamers-ricochet-bans-2026-10-31]] |
| Account security | Two-factor sign-in and email verification for accounts | Launch | [[src-activision-pc-specs-2026-09-29]] |

Posts summing up the anti-cheat describe the two newer layers in plain terms: DMA detection goes after hardware-based cheats, and stronger hardware bans make it harder for banned cheaters to return on new accounts (reported) [clm_f37ae5c8ce1d] [clm_754b64a3d133].

## Do you need TPM 2.0 and Secure Boot?

On a Windows PC, yes. MW4 refuses every online mode, DMZ included, until both are on, and shows a Failed Attestation Status [[src-activision-tpm-secure-boot-2026-10-21]]. Activision's Secure Attestation Wizard (version 1.3.0 of 21 Oct 2026) reads your BIOS settings and tells you what to change [[src-activision-tpm-secure-boot-2026-10-21]]. The usual culprits: AMD boards with TPM firmware 3.*.0.* need a firmware update, which Activision named the most common cause [clm_1a8b39bc4f78]; some Intel firmware needs one too; Lenovo owners who still fail after a BIOS update should contact Lenovo; Windows 10 must be 22H2 or newer; and the disk must use GPT with UEFI boot [[src-activision-tpm-secure-boot-2026-10-21]]. Players who still fail can send their system details through Activision's TPM report form [[src-gosugamers-ricochet-bans-2026-10-31]]. PC requirements in full: [[modern-warfare-4]].

## How many cheaters have been banned?

Before launch Team RICOCHET said it had issued more than 70,000 new hardware bans, that bans already in place would carry into MW4, and that about 5,000 accounts had been banned for cheating with input devices [[src-shanethegamer-ricochet-bans-2026-09-29]] [[src-gosugamers-ricochet-bans-2026-10-31]]. Activision gave no ban count with the DMA detections [[src-gameriv-ricochet-dma-2026-10-24]], and no DMZ-only figure had been published by 31 Oct 2026.

## What gets you banned?

Activision's Security and Enforcement Policy covers every Call of Duty game. It bans cheat software such as aimbots and wallhacks, unapproved input devices and lag switches, glitching, boosting, griefing, and hiding your identity or hardware [[src-activision-security-enforcement-policy-2026-07-31]]. Penalties run from warnings and temporary bans to permanent bans that can reach other titles; hardware bans cannot be appealed, and the security team can also reset your inventory or take items away [[src-activision-security-enforcement-policy-2026-07-31]]. Duplicating items and abusing exploits in DMZ count as well ([[exploiting-glitches]]) [clm_da4bc104bb5a].

## What is still open?

Failed attestation kept some PC players out at launch, mostly on AMD and Lenovo machines [clm_1a8b39bc4f78] [[src-shanethegamer-ricochet-bans-2026-09-29]], and Final Exfil's known-issues tracker still listed that trouble as confirmed and unresolved on 31 Oct 2026. How the DMA detection works has not been explained [[src-gameriv-ricochet-dma-2026-10-24]].
<!-- human:end -->
