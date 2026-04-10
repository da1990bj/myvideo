"""
推荐系统集成测试
测试推荐位管理、推荐视频管理、以及用户推荐获取的完整流程
"""

import requests
import json
from uuid import uuid4

BASE_URL = "http://localhost:8000"

class RecommendationIntegrationTest:
    def __init__(self, admin_token):
        self.admin_token = admin_token
        self.headers = {"Authorization": f"Bearer {admin_token}"}
        self.test_results = []

    def log_test(self, test_name, passed, message=""):
        """记录测试结果"""
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} | {test_name}")
        if message:
            print(f"       {message}")
        self.test_results.append({"name": test_name, "passed": passed, "message": message})

    # ================== 推荐位管理测试 ==================
    def test_create_recommendation_slot(self):
        """测试创建推荐位"""
        print("\n📍 推荐位管理测试")
        print("-" * 60)

        data = {
            "slot_name": f"test_slot_{uuid4().hex[:8]}",
            "display_title": "测试推荐位",
            "max_items": 10,
            "recommendation_strategy": "manual_first"
        }

        try:
            resp = requests.post(
                f"{BASE_URL}/admin/recommendation-slots",
                headers=self.headers,
                json=data
            )
            passed = resp.status_code == 201
            self.log_test("创建推荐位", passed, f"状态码: {resp.status_code}")
            if passed:
                self.created_slot = resp.json()
                return self.created_slot
        except Exception as e:
            self.log_test("创建推荐位", False, str(e))
        return None

    def test_list_recommendation_slots(self):
        """测试获取推荐位列表"""
        try:
            resp = requests.get(
                f"{BASE_URL}/admin/recommendation-slots",
                headers=self.headers
            )
            passed = resp.status_code == 200 and isinstance(resp.json(), list)
            self.log_test("获取推荐位列表", passed, f"获得 {len(resp.json())} 个推荐位")
        except Exception as e:
            self.log_test("获取推荐位列表", False, str(e))

    def test_update_recommendation_slot(self):
        """测试更新推荐位"""
        if not hasattr(self, 'created_slot'):
            print("⏭️  跳过: 未创建测试推荐位")
            return

        update_data = {
            "display_title": "更新的测试推荐位",
            "max_items": 15
        }

        try:
            resp = requests.put(
                f"{BASE_URL}/admin/recommendation-slots/{self.created_slot['id']}",
                headers=self.headers,
                json=update_data
            )
            passed = resp.status_code in [200, 201]
            self.log_test("更新推荐位", passed, f"状态码: {resp.status_code}")
        except Exception as e:
            self.log_test("更新推荐位", False, str(e))

    # ================== 推荐视频管理测试 ==================
    def test_create_video_recommendation(self, video_id):
        """测试创建视频推荐"""
        print("\n🎬 推荐视频管理测试")
        print("-" * 60)

        data = {
            "video_id": video_id,
            "recommendation_type": "featured_carousel",
            "priority": 5,
            "reason": "测试推荐"
        }

        try:
            resp = requests.post(
                f"{BASE_URL}/admin/recommendations",
                headers=self.headers,
                json=data
            )
            passed = resp.status_code == 201
            self.log_test("创建视频推荐", passed, f"状态码: {resp.status_code}")
            if passed:
                self.created_recommendation = resp.json()
                return self.created_recommendation
        except Exception as e:
            self.log_test("创建视频推荐", False, str(e))
        return None

    def test_list_recommendations(self):
        """测试获取推荐列表"""
        try:
            resp = requests.get(
                f"{BASE_URL}/admin/recommendations",
                headers=self.headers
            )
            passed = resp.status_code == 200
            data = resp.json()
            # 检查返回的推荐是否包含视频信息
            has_video_info = False
            if isinstance(data, list) and len(data) > 0:
                has_video_info = 'video' in data[0]

            self.log_test(
                "获取推荐列表",
                passed and has_video_info,
                f"获得 {len(data)} 个推荐, 包含视频信息: {has_video_info}"
            )
        except Exception as e:
            self.log_test("获取推荐列表", False, str(e))

    def test_delete_recommendation(self):
        """测试删除推荐"""
        if not hasattr(self, 'created_recommendation'):
            print("⏭️  跳过: 未创建测试推荐")
            return

        try:
            resp = requests.delete(
                f"{BASE_URL}/admin/recommendations/{self.created_recommendation['id']}",
                headers=self.headers
            )
            passed = resp.status_code in [200, 204]
            self.log_test("删除推荐", passed, f"状态码: {resp.status_code}")
        except Exception as e:
            self.log_test("删除推荐", False, str(e))

    # ================== 用户推荐查询测试 ==================
    def test_get_user_recommendations(self):
        """测试获取用户推荐"""
        print("\n👤 用户推荐查询测试")
        print("-" * 60)

        try:
            resp = requests.get(
                f"{BASE_URL}/recommendations",
                params={"slot_name": "home_carousel", "limit": 5}
            )
            passed = resp.status_code == 200
            data = resp.json()
            has_recommendations = 'recommendations' in data

            self.log_test(
                "获取用户推荐",
                passed and has_recommendations,
                f"状态码: {resp.status_code}, 包含推荐: {has_recommendations}"
            )
        except Exception as e:
            self.log_test("获取用户推荐", False, str(e))

    def test_track_recommendation_click(self):
        """测试记录推荐点击"""
        try:
            test_video_id = str(uuid4())
            resp = requests.post(
                f"{BASE_URL}/recommendations/click",
                headers={},
                json={
                    "video_id": test_video_id,
                    "slot_name": "home_carousel",
                    "impression_rank": 1
                }
            )
            passed = resp.status_code in [200, 201]
            self.log_test("记录推荐点击", passed, f"状态码: {resp.status_code}")
        except Exception as e:
            self.log_test("记录推荐点击", False, str(e))

    # ================== 分析端点测试 ==================
    def test_recommendation_analytics(self):
        """测试推荐分析端点"""
        print("\n📊 推荐分析测试")
        print("-" * 60)

        try:
            resp = requests.get(
                f"{BASE_URL}/admin/recommendations/analytics",
                headers=self.headers
            )
            passed = resp.status_code == 200
            data = resp.json()
            has_required_fields = all(k in data for k in [
                'user_engagement', 'top_performing', 'by_source'
            ])

            self.log_test(
                "获取推荐分析",
                passed and has_required_fields,
                f"状态码: {resp.status_code}, 包含必需字段: {has_required_fields}"
            )
        except Exception as e:
            self.log_test("获取推荐分析", False, str(e))

    def print_summary(self):
        """打印测试总结"""
        print("\n" + "="*60)
        print("测试总结")
        print("="*60)

        total = len(self.test_results)
        passed = sum(1 for t in self.test_results if t['passed'])
        failed = total - passed

        print(f"\n总测试数: {total}")
        print(f"✅ 通过: {passed}")
        print(f"❌ 失败: {failed}")
        print(f"通过率: {(passed/total*100):.2f}%")

        if failed > 0:
            print("\n失败的测试:")
            for test in self.test_results:
                if not test['passed']:
                    print(f"  - {test['name']}: {test['message']}")

def run_integration_tests(admin_token):
    """运行集成测试"""
    print("\n🧪 开始推荐系统集成测试...")
    print("="*60)

    # 先获取一个视频ID用于测试
    try:
        resp = requests.get(f"{BASE_URL}/videos?size=1")
        if resp.status_code == 200:
            videos = resp.json()
            test_video_id = videos[0]['id'] if videos else str(uuid4())
        else:
            test_video_id = str(uuid4())
    except:
        test_video_id = str(uuid4())

    tester = RecommendationIntegrationTest(admin_token)

    try:
        # 推荐位管理测试
        tester.test_create_recommendation_slot()
        tester.test_list_recommendation_slots()
        tester.test_update_recommendation_slot()

        # 推荐视频管理测试
        tester.test_create_video_recommendation(test_video_id)
        tester.test_list_recommendations()
        tester.test_delete_recommendation()

        # 用户推荐查询测试
        tester.test_get_user_recommendations()
        tester.test_track_recommendation_click()

        # 分析测试
        tester.test_recommendation_analytics()

        # 打印总结
        tester.print_summary()

        print("\n✅ 集成测试完成！")

    except Exception as e:
        print(f"\n❌ 测试过程出错: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # 需要传入管理员token
    import sys
    if len(sys.argv) < 2:
        print("用法: python test_recommendation_integration.py <admin_token>")
        sys.exit(1)

    admin_token = sys.argv[1]
    run_integration_tests(admin_token)
