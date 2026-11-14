-- 办公室离谱辩论会 · seekdb 初始化脚本
-- 用途一：作为 Dify 部署时的元数据库（按常规 Dify 部署配置指向本实例）。
-- 用途二：持久化玩家记忆，供 PowerContext 导出快照 memory-store.json 使用。
-- 兼容 MySQL 协议，直接在 seekdb 客户端执行即可；全部为示例数据，不含任何真实凭据。

CREATE DATABASE IF NOT EXISTS debate_memory;
USE debate_memory;

CREATE TABLE IF NOT EXISTS player_memory (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  player VARCHAR(64) NOT NULL,
  topic VARCHAR(255) NOT NULL,
  round_no INT NOT NULL DEFAULT 1,
  content TEXT NOT NULL,
  source VARCHAR(32) NOT NULL DEFAULT 'powercontext',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FULLTEXT KEY ft_content (content),
  KEY idx_player_round (player, round_no)
);

-- 与 memory-store.json 保持一致的两轮示例记忆。
INSERT INTO player_memory (player, topic, round_no, content, source) VALUES
  ('小林', '午休该不该关灯', 1,
   '第一轮：小林坚持关灯是办公室的基本礼仪，主持人把「关灯派」比作暗夜骑士；本轮新梗：关灯侠的披风是遮光布。',
   'powercontext'),
  ('小林', '群里回收到算不算已读', 2,
   '第二轮：小林认为「收到」只是礼貌回声，主持人接上上次的关灯侠梗，说遮光布也盖不住已读不回；本轮新梗：已读回声兽。',
   'powercontext');

-- 按玩家取回最近记忆，导出后填入 DSL 开始节点的 memory_context。
SELECT player, topic, round_no, content
FROM player_memory
WHERE player = '小林'
ORDER BY round_no DESC
LIMIT 5;

-- 混合检索示例：全文命中 + 结构化条件，用于核对 seekdb 在检索环节的实际作用。
SELECT player, topic, content,
       MATCH(content) AGAINST ('关灯侠' IN NATURAL LANGUAGE MODE) AS relevance
FROM player_memory
WHERE MATCH(content) AGAINST ('关灯侠' IN NATURAL LANGUAGE MODE)
  AND player = '小林'
ORDER BY relevance DESC;

-- 重置方法：清空示例数据后可重新执行上面的 INSERT 恢复初始状态。
-- TRUNCATE TABLE player_memory;
