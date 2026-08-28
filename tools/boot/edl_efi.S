/* SPDX-License-Identifier: GPL-2.0+ */
/* Minimal ARM64 EFI application for one-shot Qualcomm EDL recovery. */

	.section .text, "ax"
	.align 4
	.global efi_main
	.type efi_main, %function
efi_main:
	/* Preserve the EFI caller's nonvolatile register and return address. */
	stp	x19, x30, [sp, #-16]!

	/* Set the standard Qualcomm EDL cookie. */
	movz	x0, #0x0502
	movk	x0, #0x8200, lsl #16
	mov	x1, #2
	movz	x2, #0x3000
	movk	x2, #0x01fd, lsl #16
	mov	x3, #1
	mov	x4, #0
	mov	x5, #0
	mov	x6, #0
	mov	x7, #0
	smc	#0
	cbnz	x0, 1f

	/* Disable SDI and reset. Keep this adjacent to the cookie SMC. */
	movz	x0, #0x0108
	movk	x0, #0x8200, lsl #16
	mov	x1, #1
	mov	x2, #1
	mov	x3, #0
	mov	x4, #0
	mov	x5, #0
	mov	x6, #0
	mov	x7, #0
	smc	#0

1:	ldp	x19, x30, [sp], #16
	/* EFI_DEVICE_ERROR: reaching the caller means the reset failed. */
	mov	x0, #7
	movk	x0, #0x8000, lsl #48
	ret
	.size efi_main, . - efi_main

	/* Qualcomm's production UEFI rejects PE images without a relocation table,
	 * even though this application contains only position-independent code.
	 * A block of IMAGE_REL_BASED_ABSOLUTE entries is the PE-defined no-op form.
	 */
	.section .reloc, "a"
	.align 2
	.long 0x1000
	.long 12
	.short 0
	.short 0
