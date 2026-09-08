/* SPDX-License-Identifier: GPL-2.0 */
/*
 * NVMe tracepoint context types, the part of the nvme_core module's BTF
 * that nvme_bpf's `nvme_core_gen.h` supplies and a vmlinux dump does not.
 *
 * The upstream build generates that header from a running kernel:
 *
 *   bpftool btf dump file /sys/kernel/btf/nvme_core format c
 *
 * A Nix builder has no /sys/kernel/btf, so nvme-bpf.nix builds the same
 * header from the pinned CO-RE vmlinux.h plus this file. Only the two
 * tracepoints the programs attach to are here; take the rest out of that
 * dump, or out of drivers/nvme/host/trace.h, if a program grows a third.
 *
 * The pragma is what the generated header applies to every record: field
 * offsets become CO-RE relocations, resolved at load time against the
 * running kernel's (here: the nvme_core module's) BTF, so these
 * definitions do not have to match the target kernel byte for byte.
 */
#ifndef __NVME_CORE_TYPES_H__
#define __NVME_CORE_TYPES_H__

#ifndef BPF_NO_PRESERVE_ACCESS_INDEX
#pragma clang attribute push (__attribute__((preserve_access_index)), apply_to = record)
#endif

struct trace_event_raw_nvme_complete_rq {
	struct trace_entry ent;
	char disk[32];
	int ctrl_id;
	int qid;
	int cid;
	u64 result;
	u8 retries;
	u8 flags;
	u16 status;
	char __data[0];
};

struct trace_event_raw_nvme_setup_cmd {
	struct trace_entry ent;
	char disk[32];
	int ctrl_id;
	int qid;
	u8 opcode;
	u8 flags;
	u8 fctype;
	u16 cid;
	u32 nsid;
	bool metadata;
	u8 cdw10[24];
	char __data[0];
};

#ifndef BPF_NO_PRESERVE_ACCESS_INDEX
#pragma clang attribute pop
#endif

#endif /* __NVME_CORE_TYPES_H__ */
