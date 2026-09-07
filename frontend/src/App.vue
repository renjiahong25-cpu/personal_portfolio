<template>
  <el-container class="app-container">
    <!-- 左侧导航 -->
    <el-aside width="220px" class="sidebar">
      <div class="logo">
        <el-icon :size="28" color="#409EFF"><Ship /></el-icon>
        <div class="logo-text">
          <div class="logo-title">跨境物流规则Agent</div>
          <div class="logo-sub">中国出口德国清关</div>
        </div>
      </div>

      <el-menu
        :default-active="activeMenu"
        router
        class="side-menu"
        background-color="transparent"
        text-color="#c0c4cc"
        active-text-color="#ffffff"
      >
        <el-menu-item index="/chat">
          <el-icon><ChatDotRound /></el-icon>
          <span>智能问答</span>
        </el-menu-item>
        <el-menu-item index="/knowledge">
          <el-icon><Collection /></el-icon>
          <span>知识库管理</span>
        </el-menu-item>
        <el-menu-item index="/spider">
          <el-icon><Connection /></el-icon>
          <span>爬虫配置</span>
        </el-menu-item>
        <el-menu-item index="/eval">
          <el-icon><DataAnalysis /></el-icon>
          <span>评测监控</span>
        </el-menu-item>
      </el-menu>

      <div class="sidebar-footer">
        <el-tag size="small" type="warning" effect="plain">RAG 轻量化 Agent</el-tag>
      </div>
    </el-aside>

    <!-- 右侧主内容 -->
    <el-container class="main-area">
      <el-header class="header">
        <div class="header-title">{{ currentTitle }}</div>
        <div class="header-right">
          <el-tooltip content="服务状态" placement="bottom">
            <el-tag type="success" effect="light" size="small">
              <span class="status-dot"></span> 后端在线
            </el-tag>
          </el-tooltip>
        </div>
      </el-header>

      <el-main class="content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const activeMenu = computed(() => route.path)

const currentTitle = computed(() => route.meta.title || '跨境物流规则智能Agent')
</script>

<style>
/* 全局样式 */
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html,
body,
#app {
  height: 100%;
  font-family: 'Helvetica Neue', Helvetica, 'PingFang SC', 'Microsoft YaHei', Arial, sans-serif;
  background: #f0f2f5;
}

.app-container {
  height: 100vh;
}

/* 左侧导航 */
.sidebar {
  background: linear-gradient(180deg, #1f2d3d 0%, #2b3a4a 100%);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.logo-text {
  color: #fff;
}

.logo-title {
  font-size: 15px;
  font-weight: 600;
}

.logo-sub {
  font-size: 11px;
  color: #8fa3b3;
  margin-top: 2px;
}

.side-menu {
  flex: 1;
  border-right: none;
  padding-top: 12px;
}

.side-menu .el-menu-item {
  height: 50px;
  line-height: 50px;
  margin: 4px 8px;
  border-radius: 8px;
  transition: all 0.2s;
}

.side-menu .el-menu-item:hover {
  background: rgba(255, 255, 255, 0.12);
  transform: scale(1.02);
}

.side-menu .el-menu-item.is-active {
  background: #409EFF;
  box-shadow: 0 4px 12px rgba(64, 158, 255, 0.35);
}

.sidebar-footer {
  padding: 16px;
  text-align: center;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

/* 主区域 */
.main-area {
  flex-direction: column;
  overflow: hidden;
}

.header {
  background: #fff;
  border-bottom: 1px solid #ebeef5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 60px;
  padding: 0 24px;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.05);
}

.header-title {
  font-size: 17px;
  font-weight: 600;
  color: #303133;
}

.header-right {
  display: flex;
  align-items: center;
}

.status-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #67C23A;
  margin-right: 4px;
  animation: pulse 1.5s infinite;
}

@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(103, 194, 58, 0.4); }
  50% { box-shadow: 0 0 0 6px rgba(103, 194, 58, 0); }
}

.content {
  padding: 20px;
  overflow: auto;
  background: #f0f2f5;
}

/* 通用卡片 */
.panel-card {
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  padding: 20px;
}

.card-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 16px;
  position: relative;
  padding-left: 12px;
}

.card-title::before {
  content: '';
  position: absolute;
  left: 0;
  top: 4px;
  bottom: 4px;
  width: 4px;
  border-radius: 2px;
  background: #409EFF;
}

/* 按钮统一hover效果 */
.el-button {
  transition: all 0.2s !important;
}

.el-button:hover {
  transform: scale(1.04) !important;
}

/* 滚动条美化 */
::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}

::-webkit-scrollbar-thumb {
  background: #c0c4cc;
  border-radius: 3px;
}

::-webkit-scrollbar-track {
  background: transparent;
}
</style>
