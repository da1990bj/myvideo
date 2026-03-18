"""
推荐系统性能基准测试
测试单用户推荐查询的性能、多用户并发、缓存效果等
"""

import time
import requests
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import mean, stdev

BASE_URL = "http://localhost:8000"

class RecommendationPerformanceTest:
    def __init__(self, admin_token=None):
        self.admin_token = admin_token
        self.results = {}

    def measure_time(self, func, *args, **kwargs):
        """测量函数执行时间"""
        start = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        return elapsed, result

    # ==================== 基准测试 ====================
    def test_single_user_recommendation(self, slot_name="home_carousel", limit=10):
        """测试单用户推荐查询性能"""
        print(f"\n📊 单用户推荐查询性能 (slot: {slot_name}, limit: {limit})")

        times = []
        for i in range(10):
            elapsed, resp = self.measure_time(
                requests.get,
                f"{BASE_URL}/recommendations",
                params={"slot_name": slot_name, "limit": limit}
            )
            times.append(elapsed * 1000)  # 转换为毫秒

        avg_time = mean(times)
        min_time = min(times)
        max_time = max(times)
        std_dev = stdev(times) if len(times) > 1 else 0

        print(f"  平均响应时间: {avg_time:.2f}ms")
        print(f"  最小响应时间: {min_time:.2f}ms")
        print(f"  最大响应时间: {max_time:.2f}ms")
        print(f"  标准差: {std_dev:.2f}ms")

        self.results['single_user'] = {
            'avg': avg_time,
            'min': min_time,
            'max': max_time,
            'stdev': std_dev
        }

        return avg_time

    def test_multiple_slots(self):
        """测试不同推荐位的查询性能"""
        print("\n📊 多推荐位查询性能对比")

        slots = ["home_carousel", "category_featured", "sidebar_related", "trending"]
        slot_performance = {}

        for slot in slots:
            times = []
            for _ in range(5):
                elapsed, resp = self.measure_time(
                    requests.get,
                    f"{BASE_URL}/recommendations",
                    params={"slot_name": slot, "limit": 10}
                )
                times.append(elapsed * 1000)

            avg_time = mean(times)
            slot_performance[slot] = avg_time
            print(f"  {slot}: {avg_time:.2f}ms")

        self.results['multiple_slots'] = slot_performance
        return slot_performance

    def test_concurrent_requests(self, num_concurrent=50, num_iterations=3):
        """测试并发请求性能"""
        print(f"\n📊 并发性能测试 ({num_concurrent} 并发用户, {num_iterations} 轮)")

        def single_request():
            try:
                start = time.time()
                resp = requests.get(
                    f"{BASE_URL}/recommendations",
                    params={"slot_name": "home_carousel", "limit": 10},
                    timeout=10
                )
                elapsed = (time.time() - start) * 1000
                return elapsed, resp.status_code == 200
            except Exception as e:
                return None, False

        all_times = []
        total_success = 0
        total_fail = 0

        for iteration in range(num_iterations):
            print(f"  轮次 {iteration + 1}/{num_iterations}:")
            times = []

            with ThreadPoolExecutor(max_workers=num_concurrent) as executor:
                futures = [executor.submit(single_request) for _ in range(num_concurrent)]
                for future in as_completed(futures):
                    elapsed, success = future.result()
                    if elapsed:
                        times.append(elapsed)
                        if success:
                            total_success += 1
                        else:
                            total_fail += 1

            if times:
                avg = mean(times)
                print(f"    平均响应: {avg:.2f}ms | 成功: {len(times)} | 失败: {num_concurrent - len(times)}")
                all_times.extend(times)

        if all_times:
            total_avg = mean(all_times)
            total_min = min(all_times)
            total_max = max(all_times)
            success_rate = (total_success / (total_success + total_fail) * 100) if (total_success + total_fail) > 0 else 0

            print(f"\n  总体统计:")
            print(f"    平均响应: {total_avg:.2f}ms")
            print(f"    最小响应: {total_min:.2f}ms")
            print(f"    最大响应: {total_max:.2f}ms")
            print(f"    成功率: {success_rate:.2f}%")

            self.results['concurrent'] = {
                'avg': total_avg,
                'min': total_min,
                'max': total_max,
                'success_rate': success_rate
            }

    def test_cache_effectiveness(self):
        """测试缓存效果 - 同样查询的响应时间对比"""
        print("\n📊 缓存效果测试")

        # 第一次查询（冷调用）
        cold_times = []
        for _ in range(3):
            elapsed, _ = self.measure_time(
                requests.get,
                f"{BASE_URL}/recommendations",
                params={"slot_name": "home_carousel", "limit": 10}
            )
            cold_times.append(elapsed * 1000)

        cold_avg = mean(cold_times)

        # 等待一秒
        time.sleep(1)

        # 后续查询（热调用）
        hot_times = []
        for _ in range(10):
            elapsed, _ = self.measure_time(
                requests.get,
                f"{BASE_URL}/recommendations",
                params={"slot_name": "home_carousel", "limit": 10}
            )
            hot_times.append(elapsed * 1000)

        hot_avg = mean(hot_times)
        improvement = ((cold_avg - hot_avg) / cold_avg * 100) if cold_avg > 0 else 0

        print(f"  冷调用平均: {cold_avg:.2f}ms")
        print(f"  热调用平均: {hot_avg:.2f}ms")
        print(f"  性能提升: {improvement:.2f}%")

        self.results['cache'] = {
            'cold_avg': cold_avg,
            'hot_avg': hot_avg,
            'improvement': improvement
        }

    def test_pagination_performance(self):
        """测试分页性能"""
        print("\n📊 分页性能测试")

        pages = [1, 5, 10, 20]
        page_times = {}

        for page in pages:
            times = []
            for _ in range(5):
                elapsed, _ = self.measure_time(
                    requests.get,
                    f"{BASE_URL}/recommendations",
                    params={"slot_name": "home_carousel", "limit": 10}
                )
                times.append(elapsed * 1000)

            avg = mean(times)
            page_times[f"page_{page}"] = avg
            print(f"  分页 {page}: {avg:.2f}ms")

        self.results['pagination'] = page_times

    def print_summary(self):
        """打印测试总结"""
        print("\n" + "="*60)
        print("📈 推荐系统性能测试总结")
        print("="*60)

        print("\n✅ 测试项目:")
        print(json.dumps(self.results, indent=2, default=str))

        # 性能评估
        print("\n🎯 性能评估:")
        if 'single_user' in self.results:
            avg = self.results['single_user']['avg']
            if avg < 50:
                print(f"  ✓ 单用户响应时间 {avg:.2f}ms - 优秀 (< 50ms)")
            elif avg < 100:
                print(f"  ✓ 单用户响应时间 {avg:.2f}ms - 良好 (< 100ms)")
            elif avg < 200:
                print(f"  ⚠ 单用户响应时间 {avg:.2f}ms - 一般 (< 200ms)")
            else:
                print(f"  ✗ 单用户响应时间 {avg:.2f}ms - 需优化 (> 200ms)")

        if 'concurrent' in self.results:
            success = self.results['concurrent']['success_rate']
            if success > 99:
                print(f"  ✓ 并发成功率 {success:.2f}% - 优秀")
            elif success > 95:
                print(f"  ✓ 并发成功率 {success:.2f}% - 良好")
            else:
                print(f"  ⚠ 并发成功率 {success:.2f}% - 需优化")

        if 'cache' in self.results:
            improvement = self.results['cache']['improvement']
            print(f"  缓存性能提升: {improvement:.2f}%")

def run_all_tests():
    """运行所有性能测试"""
    print("\n🚀 开始推荐系统性能测试...")
    print("="*60)

    tester = RecommendationPerformanceTest()

    try:
        # 运行所有测试
        tester.test_single_user_recommendation()
        tester.test_multiple_slots()
        tester.test_concurrent_requests(num_concurrent=30, num_iterations=2)
        tester.test_cache_effectiveness()
        tester.test_pagination_performance()

        # 打印总结
        tester.print_summary()

        print("\n✅ 所有性能测试完成！")

    except Exception as e:
        print(f"\n❌ 测试出错: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_all_tests()
