<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const rows = ref([])
const camps = ref([])
const tags = ref([])
const form = ref({ event_type: 'redeem', coupon_code: '', campaign_code: 'NEW10', amount: '', tag: '' })
const msg = ref('')
const err = ref('')

async function load() {
  err.value = ''
  try {
    camps.value = await api.campaigns()
    tags.value = await api.tags()
    rows.value = await api.events()
    if (!form.value.campaign_code && camps.value[0]) form.value.campaign_code = camps.value[0].code
  } catch (e) { err.value = e.message }
}
async function submit() {
  msg.value = ''; err.value = ''
  try {
    const body = { ...form.value }
    if (body.amount === '') delete body.amount
    else body.amount = Number(body.amount)
    body.tag = (body.tag || '').trim()
    const r = await api.addEvent(body)
    msg.value = `已写入事件 #${r.event_id}` + (r.fact_id ? ` / 事实 #${r.fact_id}` : '') + (r.tag ? ` · tag ${r.tag}` : '')
    form.value.coupon_code = ''
    await load()
  } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
<template>
  <div class="panel">
    <div class="row">
      <label>类型
        <select v-model="form.event_type">
          <option value="issue">发放</option>
          <option value="redeem">核销</option>
          <option value="void">作废</option>
        </select>
      </label>
      <label>券码 <input v-model="form.coupon_code" placeholder="C-2xxx" /></label>
      <label>活动
        <select v-model="form.campaign_code">
          <option v-for="c in camps" :key="c.code" :value="c.code">{{ c.code }} · {{ c.title }}</option>
        </select>
      </label>
      <label>金额 <input v-model="form.amount" placeholder="可空=面额" /></label>
      <label>tag（渠道/场景）
        <input v-model="form.tag" list="tag-options" placeholder="如 store / app" />
        <datalist id="tag-options">
          <option v-for="t in tags" :key="t" :value="t" />
        </datalist>
      </label>
      <button type="button" @click="submit">写入事件</button>
      <button type="button" class="ghost" @click="load">刷新</button>
    </div>
    <p class="msg" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>
    <p class="msg">tag 是核销发生的渠道/场景，不是活动 campaign；日闸一旦关闭，任何 tag 的核销都不能再写入。</p>
  </div>
  <div class="panel">
    <table>
      <thead><tr><th>ID</th><th>类型</th><th>券码</th><th>活动</th><th>tag</th><th>金额</th><th>业务日</th><th>时刻</th></tr></thead>
      <tbody>
        <tr v-for="r in rows" :key="r.id">
          <td>{{ r.id }}</td>
          <td><span class="tag" :class="{ warn: r.event_type==='redeem' }">{{ r.event_type }}</span></td>
          <td>{{ r.coupon_code }}</td>
          <td>{{ r.campaign_code }}</td>
          <td><span class="tag">{{ r.tag || '（空）' }}</span></td>
          <td>{{ r.amount }}</td>
          <td>{{ r.business_day }}</td>
          <td>{{ r.occurred_at }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
