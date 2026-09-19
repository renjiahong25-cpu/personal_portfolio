export const WORLD_COUNTRIES = [
  { code: 'VN', label: '越南', map_name: 'Vietnam', coord: [106.8, 15.9] },
  { code: 'SG', label: '新加坡', map_name: 'Singapore', coord: [103.8, 1.35] },
  { code: 'MY', label: '马来西亚', map_name: 'Malaysia', coord: [109.0, 3.5] },
  { code: 'TH', label: '泰国', map_name: 'Thailand', coord: [101.0, 15.0] },
  { code: 'MX', label: '墨西哥', map_name: 'Mexico', coord: [-102.5, 23.5] },
  { code: 'CN', label: '中国', map_name: 'China', coord: [104.0, 35.0] },
  { code: 'US', label: '美国', map_name: 'United States', coord: [-98.0, 39.0] },
  { code: 'DE', label: '德国', map_name: 'Germany', coord: [10.4, 51.1] },
  { code: 'EU', label: '欧盟', map_name: 'Germany', coord: [10.4, 51.1] },
]

export const HUB_COUNTRIES = WORLD_COUNTRIES.filter((c) =>
  ['VN', 'SG', 'MY', 'TH', 'MX'].includes(c.code)
)

export const WORLD_MFN_COUNTRIES = HUB_COUNTRIES

export function findCountry(code) {
  const c = String(code || '').toUpperCase()
  return WORLD_COUNTRIES.find((x) => x.code === c)
}

export function countryLabel(code) {
  const c = findCountry(code)
  return c ? c.label : String(code || '').toUpperCase()
}

export function toWorldRoute(codes) {
  return (codes || []).map((c) => findCountry(c)?.code || String(c).toUpperCase())
}

export const ROUTE_PRESET_LABELS = {
  'CN->VN->US': '中国 → 越南 → 美国',
  'CN->MX->US': '中国 → 墨西哥 → 美国',
  'CN->TH->US': '中国 → 泰国 → 美国',
  'CN->SG->US': '中国 → 新加坡 → 美国',
  'CN->MY->US': '中国 → 马来西亚 → 美国',
  'CN->VN->MX->US': '中国 → 越南 → 墨西哥 → 美国',
  'CN->MX->VN->US': '中国 → 墨西哥 → 越南 → 美国',
  'CN->VN->TH->US': '中国 → 越南 → 泰国 → 美国',
  'CN->TH->MY->US': '中国 → 泰国 → 马来西亚 → 美国',
  'CN->SG->MY->US': '中国 → 新加坡 → 马来西亚 → 美国',
}

export const ROUTE_PRESETS = [
  ['CN', 'VN', 'US'],
  ['CN', 'MX', 'US'],
  ['CN', 'TH', 'US'],
  ['CN', 'SG', 'US'],
  ['CN', 'MY', 'US'],
  ['CN', 'VN', 'MX', 'US'],
  ['CN', 'MX', 'VN', 'US'],
  ['CN', 'VN', 'TH', 'US'],
  ['CN', 'TH', 'MY', 'US'],
  ['CN', 'SG', 'MY', 'US'],
]