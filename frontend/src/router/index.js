import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    redirect: '/chat',
  },
  {
    path: '/chat',
    name: 'Chat',
    component: () => import('@/views/ChatView.vue'),
    meta: { title: '智能问答' },
  },
  {
    path: '/knowledge',
    name: 'Knowledge',
    component: () => import('@/views/KnowledgeView.vue'),
    meta: { title: '知识库管理' },
  },
  {
    path: '/spider',
    name: 'Spider',
    component: () => import('@/views/SpiderView.vue'),
    meta: { title: '爬虫配置' },
  },
  {
    path: '/eval',
    name: 'Eval',
    component: () => import('@/views/EvalView.vue'),
    meta: { title: '评测监控' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
