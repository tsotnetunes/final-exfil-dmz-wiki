# Activision Support: Trusted Platform Module (TPM) 2.0 and Secure Boot for Call of Duty
URL: https://support.activision.com/articles/trusted-platform-module-and-secure-boot
Captured: 2026-10-31 (page dated 21 Oct 2026)
Trust: official

Facts taken, in our words:
- Modern Warfare 4 on a Windows PC needs both TPM 2.0 and Secure Boot; a PC without them cannot play any online mode of MW4. (Black Ops 7 and Warzone only lock some modes, such as Ranked Play, and may matchmake such players separately.)
- A PC that fails gets an in-game notice and a Failed Attestation Status; closing or declining the Windows permission prompt for the game's broker installer or its key-enrolment helper blocks play entirely.
- The Secure Attestation Wizard, version 1.3.0 updated 21 Oct 2026, reads the PC's BIOS information and reports which settings to change: TPM 2.0, Secure Boot, GPT disk layout, UEFI boot mode, firmware updates, permission prompts and event-log failures.
- AMD motherboards whose TPM firmware version is 3.*.0.* are incompatible until a firmware update (the page shows one 3.92.0.5 version needing it and a 3.92.5.5 version that is fine); some Intel firmware versions may need an update too; Lenovo owners who still fail after a BIOS update are told to contact Lenovo; Windows 10 must be version 22H2 or later; MBR disks must be converted to GPT and the boot mode set to UEFI.
- Purpose: to stop low-level cheats by making sure only trusted software loads when the PC starts, working together with RICOCHET Anti-Cheat.
- The page covers Windows PCs only; it does not name DMZ separately (DMZ is one of MW4's online modes).
