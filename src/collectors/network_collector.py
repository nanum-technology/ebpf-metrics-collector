# src/collectors/network_collector.py

import socket
import struct
import time
from typing import Dict, Any
from .base_collector import BaseCollector
import structlog

logger = structlog.get_logger()


class NetworkCollector(BaseCollector):
    """네트워크 메트릭 수집기

    연결 4-tuple(출발지/목적지 IP, sport, dport) + 프로토콜 + 방향 단위로 커널 맵에서 집계하고,
    매 수집 주기마다 BPF_HASH 스냅샷을 읽어 비운다 (perf 버퍼 폴링 없음 — 저오버헤드 구조).

      TCP   : kprobe tcp_sendmsg(out) · tcp_cleanup_rbuf(in) — 패킷/바이트, smoothed RTT
              tracepoint tcp:tcp_retransmit_skb — 재전송 수 (→ loss_rate)
      UDP   : kprobe udp_sendmsg(out) · skb_consume_udp(in)            (2026-10 추가)
      ICMP  : kprobe icmp_rcv(in) · __icmp_send(out, 패킷 수만)         (2026-10 추가)
      드롭  : tracepoint skb:kfree_skb — 노드 단위 합계
      DDoS  : 규칙 기반 — 수신 pps · 수신 출발지 IP 종류 수가 임계를 넘으면 노드 요약 레코드에 표시
      임시 포트: ephemeral_port_min(기본 32768) 이상 포트는 커널 키에서 접어 태그 "eph" 로 (2026-10-13 — 시계열 폭증 대응)
    """

    PROTO_NAMES = {6: "TCP", 17: "UDP", 1: "ICMP"}

    def get_ebpf_program(self) -> str:
        return "#define EPH_MIN %d\n" % int(self.ephemeral_port_min) + """
#include <uapi/linux/ptrace.h>
#include <net/sock.h>
#include <linux/tcp.h>
#include <linux/ip.h>
#include <linux/skbuff.h>
#include <bcc/proto.h>

// 연결 식별자 (4-tuple + protocol + direction)
struct conn_key_t {
    u32 saddr;
    u32 daddr;
    u16 sport;       // local port  (host byte order)
    u16 dport;       // remote port (network byte order, 파이썬에서 ntohs)
    u8  protocol;    // 6 TCP / 17 UDP / 1 ICMP
    u8  direction;   // 0 = out(송신), 1 = in(수신)
};

// 연결별 통계
struct conn_stats_t {
    u64 packet_count;
    u64 byte_count;
    u64 retransmit_count;   // TCP out 만
    u32 srtt_us;            // TCP smoothed RTT (<<3 된 값, 마지막 관측)
};

BPF_HASH(conn_stats, struct conn_key_t, struct conn_stats_t);

// 노드 단위 드롭 통계 (pid 키 — 기존 동작 유지)
struct drop_stats_t {
    u64 drop_count;
};
BPF_HASH(drop_stats, u32, struct drop_stats_t);

// 임시 포트(ephemeral port) 접기 — 커널 집계 키 단계에서 EPH_MIN 이상 포트를 0 으로 바꾼다.
// 연결마다 새로 붙는 클라이언트 쪽 포트가 시계열을 폭증시키므로(하루 45만 → 153, 2026-10-13 측정),
// 맵에 들어가기 전에 접어 맵 항목 수 · 사용자 공간 전달량 · DB 시계열 수를 함께 줄인다. EPH_MIN 0 이면 끈다.
static inline void fold_ephemeral(struct conn_key_t *key) {
    if (EPH_MIN == 0) return;
    if (key->sport >= EPH_MIN) key->sport = 0;                    // host order
    if (bpf_ntohs(key->dport) >= EPH_MIN) key->dport = 0;         // net order
}

static inline void fill_key(struct conn_key_t *key, struct sock *sk, u8 protocol, u8 direction) {
    bpf_probe_read_kernel(&key->saddr, sizeof(key->saddr), &sk->__sk_common.skc_rcv_saddr);
    bpf_probe_read_kernel(&key->daddr, sizeof(key->daddr), &sk->__sk_common.skc_daddr);
    bpf_probe_read_kernel(&key->sport, sizeof(key->sport), &sk->__sk_common.skc_num);   // host order
    bpf_probe_read_kernel(&key->dport, sizeof(key->dport), &sk->__sk_common.skc_dport); // net order
    key->protocol = protocol;
    key->direction = direction;
    fold_ephemeral(key);
}

// lookup_or_init 매크로는 실패 시 `return 0` 을 하므로 헬퍼는 int 를 돌려줘야 한다
static inline int account(struct conn_key_t *key, u64 bytes) {
    struct conn_stats_t zero = {}, *stats;
    stats = conn_stats.lookup_or_init(key, &zero);
    if (stats) {
        stats->packet_count++;
        stats->byte_count += bytes;
    }
    return 0;
}

static inline void record_srtt(struct conn_key_t *key, struct sock *sk) {
    struct tcp_sock *tp = (struct tcp_sock *)sk;
    u32 srtt = 0;
    bpf_probe_read_kernel(&srtt, sizeof(srtt), &tp->srtt_us);
    if (srtt == 0) return;
    struct conn_stats_t *stats = conn_stats.lookup(key);
    if (stats) {
        stats->srtt_us = srtt;
    }
}

// ── TCP ─────────────────────────────────────────────────────────────────────
int trace_tcp_sendmsg(struct pt_regs *ctx, struct sock *sk, struct msghdr *msg, size_t size) {
    struct conn_key_t key = {};
    fill_key(&key, sk, 6, 0);
    account(&key, size);
    record_srtt(&key, sk);
    return 0;
}

// tcp_cleanup_rbuf(sk, copied) 의 copied 가 실제 수신 바이트
int trace_tcp_cleanup_rbuf(struct pt_regs *ctx, struct sock *sk, int copied) {
    if (copied <= 0) {
        return 0;
    }
    struct conn_key_t key = {};
    fill_key(&key, sk, 6, 1);
    account(&key, copied);
    record_srtt(&key, sk);
    return 0;
}

// TCP 재전송 — saddr/daddr 는 sockaddr_in 바이트열 (family 2B · port 2B · addr 4B), sport/dport 는 host order
TRACEPOINT_PROBE(tcp, tcp_retransmit_skb) {
    struct conn_key_t key = {};
    __builtin_memcpy(&key.saddr, ((char *)args->saddr) + 4, sizeof(key.saddr));
    __builtin_memcpy(&key.daddr, ((char *)args->daddr) + 4, sizeof(key.daddr));
    key.sport = args->sport;
    key.dport = bpf_htons(args->dport);
    key.protocol = 6;
    key.direction = 0;
    fold_ephemeral(&key);

    struct conn_stats_t zero = {}, *stats;
    stats = conn_stats.lookup_or_init(&key, &zero);
    if (stats) {
        stats->retransmit_count++;
    }
    return 0;
}

// ── UDP ─────────────────────────────────────────────────────────────────────
int trace_udp_sendmsg(struct pt_regs *ctx, struct sock *sk, struct msghdr *msg, size_t len) {
    struct conn_key_t key = {};
    fill_key(&key, sk, 17, 0);
    account(&key, len);
    return 0;
}

// skb_consume_udp(sk, skb, len) — 수신 데이터그램이 사용자에게 복사된 뒤 호출. len = 소비 바이트
int trace_skb_consume_udp(struct pt_regs *ctx, struct sock *sk, struct sk_buff *skb, int len) {
    if (len <= 0) {
        return 0;
    }
    struct conn_key_t key = {};
    fill_key(&key, sk, 17, 1);
    account(&key, len);
    return 0;
}

// ── ICMP ────────────────────────────────────────────────────────────────────
static inline int read_iphdr(struct sk_buff *skb, struct iphdr *ip) {
    unsigned char *head = NULL;
    u16 nh = 0;
    bpf_probe_read_kernel(&head, sizeof(head), &skb->head);
    bpf_probe_read_kernel(&nh, sizeof(nh), &skb->network_header);
    if (head == NULL) return -1;
    bpf_probe_read_kernel(ip, sizeof(*ip), head + nh);
    return 0;
}

// 수신 ICMP — IP 헤더에서 주소를 읽는다 (소켓 없음)
int trace_icmp_rcv(struct pt_regs *ctx, struct sk_buff *skb) {
    struct iphdr ip = {};
    if (read_iphdr(skb, &ip) < 0) return 0;
    u32 len = 0;
    bpf_probe_read_kernel(&len, sizeof(len), &skb->len);

    struct conn_key_t key = {};
    key.saddr = ip.daddr;   // 로컬 = 패킷의 목적지
    key.daddr = ip.saddr;   // 원격 = 패킷의 출발지
    key.protocol = 1;
    key.direction = 1;
    account(&key, len);
    return 0;
}

// 송신 ICMP — __icmp_send(skb_in, type, code, info, opt). skb_in 은 응답을 유발한 패킷이라
// 응답은 skb_in 의 출발지로 나간다. 바이트 수는 알 수 없어 패킷 수만 센다.
int trace_icmp_send(struct pt_regs *ctx, struct sk_buff *skb_in, int type, int code) {
    struct iphdr ip = {};
    if (read_iphdr(skb_in, &ip) < 0) return 0;

    struct conn_key_t key = {};
    key.saddr = ip.daddr;
    key.daddr = ip.saddr;
    key.protocol = 1;
    key.direction = 0;
    account(&key, 0);
    return 0;
}

// ── 드롭 ────────────────────────────────────────────────────────────────────
TRACEPOINT_PROBE(skb, kfree_skb) {
    u32 pid = bpf_get_current_pid_tgid() >> 32;
    struct drop_stats_t zero = {}, *stats;
    stats = drop_stats.lookup_or_init(&pid, &zero);
    if (stats) {
        stats->drop_count++;
    }
    return 0;
}
"""

    # (kprobe 심볼, eBPF 함수, 필수 여부) — 필수가 아닌 것은 커널에 심볼이 없어도 수집기를 죽이지 않는다
    KPROBES = [
        ("tcp_sendmsg", "trace_tcp_sendmsg", True),
        ("tcp_cleanup_rbuf", "trace_tcp_cleanup_rbuf", True),
        ("udp_sendmsg", "trace_udp_sendmsg", False),
        ("skb_consume_udp", "trace_skb_consume_udp", False),
        ("icmp_rcv", "trace_icmp_rcv", False),
        ("__icmp_send", "trace_icmp_send", False),
    ]

    def __init__(self, config: Dict[str, Any], node_info: Dict[str, str]):
        super().__init__(config, node_info)
        ddos = (config or {}).get("ddos", {}) or {}
        self.ddos_pps_threshold = float(ddos.get("inbound_pps_threshold", 20000))
        self.ddos_src_threshold = int(ddos.get("unique_src_threshold", 500))
        # 임시 포트 접기 임계 — 이 값 이상 포트는 커널 키에서 0, 태그에서 "eph". 0 이면 끈다 (Linux 기본 임시 포트 32768~)
        self.ephemeral_port_min = int((config or {}).get("ephemeral_port_min", 32768))
        self._last_collect_ts = None
        self.attached = []

    def load_ebpf(self):
        """eBPF 프로그램 로드 후 kprobe 를 명시적으로 attach.

        BaseCollector.load_ebpf()는 BPF 객체만 생성하므로 함수형 프로브는 여기서 붙인다.
        (TRACEPOINT_PROBE 는 자동 attach.) UDP/ICMP 프로브는 선택 — 실패해도 TCP 수집은 계속된다.
        """
        super().load_ebpf()
        for event, fn, required in self.KPROBES:
            try:
                self.bpf.attach_kprobe(event=event, fn_name=fn)
                self.attached.append(event)
            except Exception as e:
                if required:
                    logger.error("network_kprobe_attach_failed", event=event, error=str(e))
                    raise
                logger.warning("network_kprobe_optional_skipped", event=event, error=str(e))
        logger.info("network_kprobes_attached", events=self.attached)

    def _port_tag(self, port: int) -> str:
        """커널에서 접힌 임시 포트(0)는 "eph" 로. 접기를 끈 경우(ephemeral_port_min=0) 0 은 그대로."""
        if port == 0 and self.ephemeral_port_min:
            return "eph"
        return str(port)

    @staticmethod
    def _ipv4(addr: int) -> str:
        """커널 __be32(네트워크 바이트 순서) 값을 점 표기 IP 문자열로 변환."""
        return socket.inet_ntoa(struct.pack("I", addr & 0xffffffff))

    async def collect(self) -> Dict[str, Any]:
        """네트워크 메트릭 수집 (연결 4-tuple 단위 + 노드 요약 1건)"""
        metrics = []

        try:
            if self.bpf is None:
                return {"type": "network", "metrics": metrics}

            now = time.time()
            timestamp = int(now * 1e9)
            interval = (now - self._last_collect_ts) if self._last_collect_ts else None
            self._last_collect_ts = now

            # 노드 단위 드롭 합계
            drop_total = 0
            drop_stats = self.bpf["drop_stats"]
            for _, v in drop_stats.items():
                drop_total += v.drop_count
            drop_stats.clear()

            conn_stats = self.bpf["conn_stats"]
            inbound_packets = 0
            inbound_sources = set()

            for k, v in conn_stats.items():
                if v.packet_count == 0 and v.retransmit_count == 0:
                    continue

                proto = self.PROTO_NAMES.get(k.protocol, str(k.protocol))
                direction = "in" if k.direction == 1 else "out"
                is_icmp = k.protocol == 1

                fields = {
                    "packet_count": v.packet_count,
                    "byte_count": v.byte_count,
                    "error_count": 0,
                    "drop_count": 0,
                }
                if k.protocol == 6:
                    if direction == "out":
                        fields["retransmit_count"] = v.retransmit_count
                        fields["loss_rate"] = (v.retransmit_count / v.packet_count) if v.packet_count else 0.0
                    if v.srtt_us:
                        fields["rtt_ms"] = (v.srtt_us >> 3) / 1000.0

                if direction == "in":
                    inbound_packets += v.packet_count
                    inbound_sources.add(k.daddr)   # in 레코드의 daddr = 원격(출발지)

                metrics.append({
                    "measurement": "network_metrics",
                    "tags": {
                        **self.get_common_tags(),
                        "protocol": proto,
                        "direction": direction,
                        "src_ip": self._ipv4(k.saddr),
                        "dst_ip": self._ipv4(k.daddr),
                        "sport": "" if is_icmp else self._port_tag(k.sport),
                        "dport": "" if is_icmp else self._port_tag(socket.ntohs(k.dport)),
                    },
                    "fields": fields,
                    "timestamp": timestamp
                })

            conn_stats.clear()

            # 노드 요약 레코드 — 드롭 · 수신 pps · 출발지 종류 · DDoS 판정 (매 주기 1건)
            inbound_pps = (inbound_packets / interval) if interval and interval > 0 else 0.0
            unique_src = len(inbound_sources)
            ddos = bool(inbound_pps >= self.ddos_pps_threshold or unique_src >= self.ddos_src_threshold)
            if ddos:
                logger.warning("network_ddos_suspected", inbound_pps=round(inbound_pps, 1),
                               unique_src_count=unique_src,
                               pps_threshold=self.ddos_pps_threshold, src_threshold=self.ddos_src_threshold)
            metrics.append({
                "measurement": "network_metrics",
                "tags": {
                    **self.get_common_tags(),
                    "protocol": "ALL",
                    "direction": "in",
                    "src_ip": "",
                    "dst_ip": "",
                    "sport": "",
                    "dport": "",
                },
                "fields": {
                    "packet_count": inbound_packets,
                    "byte_count": 0,
                    "error_count": 0,
                    "drop_count": drop_total,
                    "inbound_pps": float(inbound_pps),
                    "unique_src_count": unique_src,
                    "ddos_detected": ddos,
                },
                "timestamp": timestamp
            })

            logger.debug("network_metrics_collected", count=len(metrics))

        except Exception as e:
            logger.error("network_collect_error", error=str(e))

        return {"type": "network", "metrics": metrics}
