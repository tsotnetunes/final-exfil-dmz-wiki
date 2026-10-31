---
type: mechanic
title: "Server Culling"
description: "RICOCHET's anti-wallhack layer: maps carry visibility data so the server keeps hidden enemies' positions off your machine. Used in DMZ since launch day (official, 30 Oct 2026)."
status: stable
brain_status: published
sensitivity: public
sources: [clm_1f0839d8acfd, clm_16b6ee610da3, clm_564a296f18a5, clm_08be82df1e9a, clm_d584776a9a8d, src-dexerto-server-culling-2026-10-02, src-destructoid-server-culling-2026-10-02, src-westie-dmz-ama-recap-2026-10-11, src-activision-tpm-secure-boot-2026-10-21]
aliases: ["server-side culling", "anti-wallhack", "wallhack protection", "wallhacks", "occlusion culling"]
---
<!-- generated:start -->
Server Culling is an anti-cheat mechanic introduced in Modern Warfare 4 to fight cheating. It affects matches by providing a new method to combat dishonest play across supported modes.

## How does Server Culling work in matches?

* Modern Warfare 4 introduces Server Culling as a new way to fight cheating, confirmed on 4 Oct 2026.
* Server Culling has been used in DMZ since Day One, confirmed on 30 Oct 2026.

_Generated from 2 confirmed and 0 reported claims across 2 posts and 0 patch notes; last rendered 2026-10-31._
<!-- generated:end -->

<!-- human:start -->
Server Culling is the part of RICOCHET Anti-Cheat aimed at wallhacks: the game server holds back where an opponent is until that opponent could actually be in your view, so cheat software has nothing to draw [[src-dexerto-server-culling-2026-10-02]] [[src-destructoid-server-culling-2026-10-02]]. Infinity Ward confirmed on 30 Oct 2026 that DMZ has used it since day one [clm_16b6ee610da3].

## How does it work?

A wallhack reads the enemy positions that a game normally sends to every player's machine. In Modern Warfare 4 each map carries visibility data built into its walls, buildings and other cover, and the server uses it to keep an unseen opponent's position from reaching your game until they come into view [[src-dexerto-server-culling-2026-10-02]] [[src-destructoid-server-culling-2026-10-02]]. Activision presented it as a new way to fight cheating when it announced it on 2 Oct 2026 [clm_1f0839d8acfd]. Players who do not cheat should notice no difference [[src-dexerto-server-culling-2026-10-02]].

## Where does it run?

At the announcement it covered every MW4 launch map, DMZ, and the maps and updates arriving from Season 1 on [[src-dexerto-server-culling-2026-10-02]] [[src-destructoid-server-culling-2026-10-02]]. Dexerto also wrote that Warzone is covered, while Destructoid's report did not mention Warzone, so Warzone is not confirmed here. For DMZ the question is settled: it has been active since launch [clm_16b6ee610da3].

## What else protects DMZ?

Server Culling only deals with wallhacks. The rest of RICOCHET handles other cheating: on PC the game needs TPM 2.0 and Secure Boot before it allows any online play [[src-activision-tpm-secure-boot-2026-10-21]], and the developers' Q&A also listed stronger account security [[src-westie-dmz-ama-recap-2026-10-11]]. The full picture, with DMA detection and hardware bans, is on [[ricochet-anti-cheat]].
<!-- human:end -->
