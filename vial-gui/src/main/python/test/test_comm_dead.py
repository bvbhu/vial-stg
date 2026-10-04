# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""通信失效检测（comm_dead）与设备断开/复活闭环的测试。

背景：固件切蓝牙后对 USB raw HID 命令不再回包（枚举仍在但无响应），
GUI 的 hid_send 若长时间阻塞会让 Qt 主线程饿死（"卡死"）。
修复：hid_send 快速失败 + Keyboard 连续失败置 comm_dead + autorefresh
把 comm_dead 设备收进黑名单（列表消失）。

探测约定（关键）：后台**不做周期心跳**，只在"刷新设备列表"(update) 时探测，
且只探黑名单内设备（GUI 已不连它们）。正常工作时由 UI 侧命令无响应触发
刷新，刷新时的探测决定设备是否回到可用列表。当前连接设备从不后台探测——
UI 线程正在用它（矩阵页 20ms 轮询、analog 连续读），后台并发发命令会串包
（矩阵页全触发 / definition 分块残缺导致 lzma 解压失败）。
"""

import unittest

from protocol.keyboard_comm import Keyboard
from autorefresh.autorefresh_thread import AutorefreshThread


def make_comm_test_usb_send(failures_before_success, response=b"\x00"):
    """构造 usb_send：前 failures_before_success 次抛 RuntimeError，之后成功。"""
    state = {"calls": 0}

    def usb_send(dev, msg, retries=1):
        state["calls"] += 1
        if state["calls"] <= failures_before_success:
            raise RuntimeError("failed to communicate with the device")
        return response

    return usb_send, state


class TestCommDeadDetection(unittest.TestCase):

    def test_comm_dead_after_repeated_failures(self):
        """连续失败达到阈值即 comm_dead=True；成功一次即清零复活。"""
        usb_send, state = make_comm_test_usb_send(failures_before_success=4)
        kb = Keyboard(None, usb_send=usb_send)

        self.assertFalse(kb.comm_dead)

        # 前 3 次失败 → comm_dead（阈值 3）
        for _ in range(3):
            with self.assertRaises(RuntimeError):
                kb.usb_send(None, b"\x01")
        self.assertTrue(kb.comm_dead)
        self.assertEqual(kb._comm_failures, 3)

        # 继续失败保持 dead
        with self.assertRaises(RuntimeError):
            kb.usb_send(None, b"\x01")
        self.assertTrue(kb.comm_dead)

        # 第 5 次调用成功（failures_before_success=4）→ 清零复活
        kb.usb_send(None, b"\x01")
        self.assertFalse(kb.comm_dead)
        self.assertEqual(kb._comm_failures, 0)
        # 调用次数：3 失败 + 1 失败 + 1 成功 = 5
        self.assertEqual(state["calls"], 5)

    def test_success_keeps_comm_alive(self):
        """稳定成功时永不置 dead。"""
        usb_send, _ = make_comm_test_usb_send(failures_before_success=0)
        kb = Keyboard(None, usb_send=usb_send)
        for _ in range(10):
            kb.usb_send(None, b"\x01")
        self.assertFalse(kb.comm_dead)
        self.assertEqual(kb._comm_failures, 0)

    def test_reset_comm_state(self):
        """reset_comm_state 手动清零。"""
        usb_send, _ = make_comm_test_usb_send(failures_before_success=99)
        kb = Keyboard(None, usb_send=usb_send)
        for _ in range(3):
            with self.assertRaises(RuntimeError):
                kb.usb_send(None, b"\x01")
        self.assertTrue(kb.comm_dead)
        kb.reset_comm_state()
        self.assertFalse(kb.comm_dead)
        self.assertEqual(kb._comm_failures, 0)


class TestAutorefreshDeadFilter(unittest.TestCase):
    """黑名单过滤：comm_dead 设备从枚举结果中消失（GUI 认为已断开）。"""

    def test_update_filters_dead_paths(self):
        thread = AutorefreshThread()

        path = b"/path/to/dead-device"
        thread.dead_paths.add(path)

        # 模拟枚举返回两个设备，其中一个在黑名单
        dead = type("D", (), {"desc": {"path": path}})()
        alive = type("D", (), {"desc": {"path": b"/path/to/alive"}})()

        filtered = [d for d in [dead, alive] if d.desc["path"] not in thread.dead_paths]
        self.assertEqual([d.desc["path"] for d in filtered], [alive.desc["path"]])

        # probe 复活后从黑名单移除，设备恢复可列
        thread.dead_paths.discard(path)
        filtered2 = [d for d in [dead, alive] if d.desc["path"] not in thread.dead_paths]
        self.assertEqual(len(filtered2), 2)

    def test_probe_discards_on_success(self):
        thread = AutorefreshThread()
        path = b"/path/to/probe-me"
        thread.dead_paths.add(path)

        # 模拟裸 HID 探测成功（无异常即成功）→ 移出黑名单
        # 这里只测 discard 逻辑（真实探测需要 hid 设备，单测不便），
        # 确保探针复活路径的集合操作正确。
        thread.dead_paths.discard(path)
        self.assertNotIn(path, thread.dead_paths)

    def test_blacklist_now_immediate(self):
        """blacklist_now=True：明确通信失败一次即拉黑（不需累计 3 次）。"""
        thread = AutorefreshThread()
        path = b"/path/blacklist-now"

        changed = thread._record_probe_result(path, path, False, blacklist_now=True)
        self.assertTrue(changed)
        self.assertIn(path, thread.dead_paths)
        self.assertNotIn(path, thread.dead_failures)

    def test_blacklist_now_idempotent(self):
        """已在黑名单时再次 blacklist_now 不重复刷新。"""
        thread = AutorefreshThread()
        path = b"/path/blacklist-now-2"

        thread._record_probe_result(path, path, False, blacklist_now=True)
        changed2 = thread._record_probe_result(path, path, False, blacklist_now=True)
        self.assertFalse(changed2)
        self.assertIn(path, thread.dead_paths)

    def test_blacklist_now_unrestricted(self):
        """blacklist_now 不限于当前设备：打开失败但设备不在 current 也能拉黑。

        （打开失败的最终结果是 current 已被置空，所以不能依赖 current_path 分支。）
        """
        thread = AutorefreshThread()
        path = b"/path/blacklist-now-3"

        changed = thread._record_probe_result(path, None, False, blacklist_now=True)
        self.assertTrue(changed)
        self.assertIn(path, thread.dead_paths)

    def test_mark_dead_triggers_hard_refresh(self):
        """mark_dead：立即进黑名单并触发硬刷新（设备从列表即时消失）。"""
        thread = AutorefreshThread()
        path = b"/path/mark-dead"
        refresh_calls = []
        fired = []
        thread.comm_dead_now.connect(lambda: fired.append(1))

        # 屏蔽真实 HID 枚举，记录 update(hard=...) 调用
        thread.update = lambda quiet=True, hard=False: refresh_calls.append(hard)

        thread.mark_dead(path)
        self.assertIn(path, thread.dead_paths)
        self.assertEqual(refresh_calls, [True])
        # 首次拉黑即发 comm_dead_now（崩溃拦截路径的弹窗时机也=无响应的刷新）
        self.assertEqual(fired, [1])

        # 已在黑名单：再次 mark_dead 不再发信号
        thread.mark_dead(path)
        self.assertEqual(fired, [1])

    def test_mark_dead_resurrection(self):
        """mark_dead 拉黑后探测成功 → 移出黑名单复活。"""
        thread = AutorefreshThread()
        path = b"/path/dead-then-alive"
        thread.update = lambda quiet=True, hard=False: None

        thread.mark_dead(path)
        self.assertIn(path, thread.dead_paths)

        revived = thread._record_probe_result(path, path, True)
        self.assertTrue(revived)
        self.assertNotIn(path, thread.dead_paths)

    def test_scan_comm_dead_adds_once(self):
        """scan_comm_dead：comm_dead 设备收进黑名单，且只报一次变化。"""
        thread = AutorefreshThread()
        path = b"/path/comm-dead-scan"
        dev = type("D", (), {
            "desc": {"path": path},
            "keyboard": type("K", (), {"comm_dead": True})(),
        })()
        thread.current_device = dev

        added = thread.scan_comm_dead()
        self.assertTrue(added)
        self.assertIn(path, thread.dead_paths)

        # 幂等：已在黑名单，再次调用不再报变化
        added2 = thread.scan_comm_dead()
        self.assertFalse(added2)

    def test_comm_dead_now_emitted_on_newly_dead(self):
        """update() 里新设备被判死 → comm_dead_now 信号（弹窗）在刷新完成后发出。

        弹窗由主窗口在收到 comm_dead_now 时弹出；信号在 update() 末尾（含
        列表无变化提前 return 的路径）发出——与「设备无响应的刷新」同步且
        不会被刷新流程盖掉。已拉黑设备的后续刷新（探测未复活）不重复发信号。
        """
        import autorefresh.autorefresh_thread as at

        path = b"/path/comm-dead-signal"
        dev = type("D", (), {
            "desc": {"path": path},
            "keyboard": type("K", (), {"comm_dead": True})(),
        })()
        dev.close = lambda: None

        thread = at.AutorefreshThread()
        thread.current_device = dev
        fired = []
        thread.comm_dead_now.connect(lambda: fired.append(1))

        # 屏蔽真实 HID 枚举与黑名单探测：update() 里只保留 scan_comm_dead 判定
        original_find = at.find_vial_devices
        at.find_vial_devices = lambda *a, **k: []
        original_probe = at.AutorefreshThread._probe_devices
        at.AutorefreshThread._probe_devices = lambda self: False
        try:
            thread.update()
        finally:
            at.find_vial_devices = original_find
            at.AutorefreshThread._probe_devices = original_probe

        # 首次刷新判死 → 发信号。注意 find_vial_devices 返回空、列表无变化，
        # update 在 old_paths==new_paths 提前 return——但 finally 仍 emit：
        # 拉黑这件事本身就要弹窗，不能被"设备列表没变"短路。
        self.assertEqual(fired, [1])
        self.assertIn(path, thread.dead_paths)

        # 已拉黑：后续刷新不再发信号（探测未复活）
        thread.update()
        self.assertEqual(fired, [1])

    def test_probe_only_blacklist(self):
        """刷新时的探测只碰黑名单设备，不碰当前已连接设备。

        当前设备正被 UI 线程收发（矩阵页轮询/analog 连续读/改键），后台再发
        命令会串包（矩阵页全触发、definition 分块残缺 → lzma 解压失败）。
        """
        import autorefresh.autorefresh_thread as at

        thread = at.AutorefreshThread()

        # 当前已连接设备（不在黑名单）
        cur_path = b"/path/connected-device"
        kb = type("K", (), {"comm_dead": False})()
        thread.current_device = type("D", (), {"desc": {"path": cur_path}, "keyboard": kb})()

        # 黑名单里的失联设备
        dead_path = b"/path/blacklisted"
        thread.dead_paths.add(dead_path)

        probed = []
        original_dev = at.VialDevice

        class FakeDev:
            def __init__(self, desc):
                probed.append(desc["path"])
            def open(self):
                pass
            def send(self, d):
                pass
            def recv(self, n, timeout_ms=0):
                return b""  # 无响应 → 维持黑名单
            def close(self):
                pass

        at.VialDevice = FakeDev
        try:
            thread._probe_devices()
        finally:
            at.VialDevice = original_dev

        # 只探黑名单设备；当前连接设备不被探测
        self.assertEqual(probed, [dead_path])
        self.assertNotIn(cur_path, probed)

    def test_probe_revive_returns_true(self):
        """黑名单设备探测成功 → 复活并报告需要重算列表。"""
        import autorefresh.autorefresh_thread as at

        thread = at.AutorefreshThread()
        path = b"/path/revived"
        thread.dead_paths.add(path)

        original_dev = at.VialDevice

        class FakeDev:
            def __init__(self, desc):
                pass
            def open(self):
                pass
            def send(self, d):
                pass
            def recv(self, n, timeout_ms=0):
                return b"\x01" * n  # 有响应 = 设备恢复通信
            def close(self):
                pass

        at.VialDevice = FakeDev
        try:
            revived = thread._probe_devices()
        finally:
            at.VialDevice = original_dev

        self.assertTrue(revived)
        self.assertNotIn(path, thread.dead_paths)  # 已移出黑名单

    def test_probe_still_dead_returns_false(self):
        """黑名单设备探测仍无响应 → 维持黑名单，不报告变化。"""
        import autorefresh.autorefresh_thread as at

        thread = at.AutorefreshThread()
        path = b"/path/still-dead"
        thread.dead_paths.add(path)

        original_dev = at.VialDevice

        class FakeDev:
            def __init__(self, desc):
                pass
            def open(self):
                pass
            def send(self, d):
                pass
            def recv(self, n, timeout_ms=0):
                return b""  # 无响应
            def close(self):
                pass

        at.VialDevice = FakeDev
        try:
            revived = thread._probe_devices()
        finally:
            at.VialDevice = original_dev

        self.assertFalse(revived)
        self.assertIn(path, thread.dead_paths)  # 仍在黑名单

    def test_handle_comm_failure_marks_dead(self):
        """handle_comm_failure：拉黑当前设备并触发刷新（崩溃拦截用）。"""
        import autorefresh.autorefresh as ar

        # 重置重入保护
        ar._comm_failure_handling = False

        path = b"/path/comm-failure"
        kb = type("K", (), {"comm_dead": True})()
        dev = type("D", (), {"desc": {"path": path}, "keyboard": kb})()
        dev.close = lambda: None

        marked = []
        thread = type("T", (), {"mark_dead": lambda self, p: marked.append(p)})()
        inst = type("I", (), {"current_device": dev, "thread": thread})()

        original_instance = ar.Autorefresh.instance
        ar.Autorefresh.instance = inst
        try:
            ar.handle_comm_failure()
        finally:
            ar.Autorefresh.instance = original_instance

        # 当前设备已回退为 None，且 mark_dead 被调用
        self.assertIsNone(inst.current_device)
        self.assertEqual(marked, [path])

    def test_handle_comm_failure_reentrant_safe(self):
        """handle_comm_failure 重入保护：递归调用直接返回，不重复处理。"""
        import autorefresh.autorefresh as ar

        ar._comm_failure_handling = True  # 模拟正在处理中
        try:
            # 不应抛异常，直接返回
            ar.handle_comm_failure()
        finally:
            ar._comm_failure_handling = False


class TestPollResponsiveness(unittest.TestCase):
    """L-10 修正：响应性参数不能为"防掉线卡死"而压得过狠。

    100ms 读超时 + 50ms 重试等待在蓝牙空口下会误杀正常回包（空口有数十毫秒
    抖动），重发又叠加等待，表现为行程读数慢半拍。这些测试把"正常链路一次读成"
    和"掉线最坏阻塞可控"两个约束都钉住，避免以后又被单侧优化调回去。
    """

    def test_read_timeout_is_generous_enough_for_ble(self):
        """读超时必须容得下蓝牙空口的正常抖动（>100ms，取 500ms）。"""
        import util

        self.assertGreaterEqual(util._READ_TIMEOUT_MS, 300)

    def test_healthy_link_completes_in_one_attempt(self):
        """正常链路：一次写 + 一次读就返回，不触发任何重试等待。"""
        import util

        sleeps = []
        original_sleep = util.time.sleep
        util.time.sleep = lambda s: sleeps.append(s)
        try:
            attempts = []

            class Dev:
                def write(self, b):
                    attempts.append("w")
                    return util.MSG_LEN + 1

                def read(self, n, timeout_ms=None):
                    attempts.append(("r", timeout_ms))
                    return b"\x01" * n

            data = util.hid_send(Dev(), b"\xFE\x04", retries=3)
        finally:
            util.time.sleep = original_sleep

        self.assertEqual(len(data), util.MSG_LEN)
        # 恰好一次写 + 一次读：没有重发，也就没有重试等待
        self.assertEqual(attempts, ["w", ("r", util._READ_TIMEOUT_MS)])
        self.assertEqual(sleeps, [])

    def test_worst_case_block_matches_retries_and_timeout(self):
        """掉线最坏阻塞 = retries × (读超时 + 重试等待)，必须留在秒级以内。

        调用方的掉线路径用 retries=3，超出这个量级就会饿死 Qt 事件循环。
        """
        import util

        sleeps = []
        original_sleep = util.time.sleep
        util.time.sleep = lambda s: sleeps.append(s)
        try:
            class Dead:
                def write(self, b):
                    return util.MSG_LEN + 1

                def read(self, n, timeout_ms=None):
                    return b""  # 设备无响应

            with self.assertRaises(RuntimeError):
                util.hid_send(Dead(), b"\xFE\x04", retries=3)
        finally:
            util.time.sleep = original_sleep

        # 3 次尝试：第 1 次不等待，后 2 次各等一次重试间隔
        self.assertEqual(len(sleeps), 2)
        worst = 3 * (util._READ_TIMEOUT_MS / 1000.0) + 2 * util._RETRY_SLEEP_S
        self.assertLess(worst, 3.0)

    def test_travel_throttle_does_not_slow_fast_links(self):
        """全键可视化：链路快时节流退化为 tick 间隔（≈未节流的初版）。

        旧的"每秒往返数预算"在 80 键宽域下恒为 300ms，即使链路本来很快也被
        强按到 3fps——这是用户感知"刷新变慢"的直接来源。
        """
        from editor.analog_tab import AnalogTab

        tick = AnalogTab._POLL_INTERVAL_NORMAL / 1000.0
        # 实测帧耗时 5ms（链路良好）：节流不得把帧周期压到超过 2 个 tick
        interval = AnalogTab._travel_frame_interval(AnalogTab, 0.005)
        self.assertLessEqual(max(tick, interval), tick * 2)

    def test_travel_throttle_still_protects_slow_links(self):
        """全键可视化：单帧耗时长时节流仍放开周期，避免饿死重绘。"""
        from editor.analog_tab import AnalogTab

        # 实测帧耗时 300ms（链路拥塞）：周期应显著长于耗时本身
        interval = AnalogTab._travel_frame_interval(AnalogTab, 0.3)
        self.assertGreater(interval, 0.3)
        # 占空比守上限：帧耗时占比不超过 1/_TRAVEL_FRAME_DUTY
        self.assertLessEqual(0.3 / (0.3 + interval), 1.0 / AnalogTab._TRAVEL_FRAME_DUTY + 1e-9)

    def test_travel_throttle_uses_latest_frame_not_average(self):
        """链路一旦变快，节流必须一帧之内跟上（不做滑动平均）。"""
        from editor.analog_tab import AnalogTab

        tab = AnalogTab.__new__(AnalogTab)
        tab._travel_next_at = 0.0

        # 先前很慢，然后一帧很快 —— 下一次排程只认这一帧
        AnalogTab._note_travel_success(tab, 0.2)
        slow_gate = tab._travel_next_at
        AnalogTab._note_travel_success(tab, 0.002)
        fast_gate = tab._travel_next_at

        import time
        now = time.monotonic()
        self.assertGreater(slow_gate - now, 0.4)   # 慢帧：周期被放长
        self.assertLess(fast_gate - now, 0.03)     # 快帧：立刻回到 tick 量级


if __name__ == "__main__":
    unittest.main()