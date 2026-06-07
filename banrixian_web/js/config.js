export const DEFAULT_API_BASE = "/api";

export const routes = {
  chat:    { title: "AI 规划",   label: "规划" },
  orders:  { title: "我的行程",  label: "行程" },
  family:  { title: "亲友画像",  label: "亲友" },
  memory:  { title: "偏好记忆",  label: "记忆" },
  profile: { title: "个人中心",  label: "我的" },
};

export const routeStyles = [
  "均衡", "美食", "亲子", "约会", "朋友", "商务",
  "休闲", "室内", "自然", "文化", "省钱", "高端",
];

export const SKILL_LABELS = {
  get_current_time:      "校准当前时间",
  decompose_goal:        "理解出行需求",
  get_memory:            "读取偏好记忆",
  get_weather:           "查询实时天气",
  analyze_user_profile:  "分析同行人画像",
  plan_time_slots:       "规划时间段",
  search_places:         "搜索真实地点",
  rank_places_for_plan:  "距离排序筛选",
  score_plans:           "比较候选路线",
  build_final_plan:      "生成最终路线",
  mock_reserve:          "生成预约意图",
  mock_create_order:     "创建订单",
};

export const EXAMPLE_QUERIES = [
  "今天下午我和老婆孩子出去玩，孩子5岁，别太远",
  "周末约会，下午3点开始，想看展或逛公园",
  "带父母出行，老人腿不太好，轻松一些",
  "和朋友下午打发时间，想吃好吃的，预算不高",
  "明天上午两小时，想去人少的地方走走",
];

export const QUICK_CHIPS = ["少走路", "带娃", "室内", "省钱", "避开排队"];
