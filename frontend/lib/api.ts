export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}
export async function api<T = Record<string, unknown>>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : {'Content-Type':'application/json','X-Requested-With':'PiusGo'},
    credentials:'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
    cache:'no-store',
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(typeof data.detail === 'string' ? data.detail : '请求失败，请检查输入或稍后重试',response.status);
  return data;
}
export async function serverApi<T>(path: string): Promise<T> {
  const r = await fetch(`${process.env.API_URL || 'http://127.0.0.1:8000'}/api${path}`,{cache:'no-store',signal:AbortSignal.timeout(8000)});
  if (!r.ok) throw new ApiError('服务暂时不可用，请稍后重试',r.status);
  return r.json();
}
export const money = (cents: number) => `¥${(cents/100).toFixed(2)}`;
export const date = (seconds: number) => new Date(seconds*1000).toLocaleString('zh-CN');
export const errorText = (e: unknown) => e instanceof Error ? e.message : '操作失败，请重试';
export type Product = {id:number;category:number;name:string;image:string;price:number;stock:number;badge:string;tags:string[];variants:{id:string;name:string;price:number;stock:number}[]};
export type Catalog = {items:Product[];categories:{id:number;name:string;image:string}[];mock:boolean};
export type Coupon = {id:string;kind:string;amount:number;minimum:number;used:number};
export type Member = {id:string;email:string;balance:number;created:number;coupons:Coupon[];orders:{total:number;paid:number};ledger:{id:string;amount:number;bonus:number;created:number}[];mock:boolean};
export type Order = {kind:'guest'|'member';paid_at:number|null;expires_at:number;email_status:'queued'|'sending'|'sent'|'mock_delivered'|'failed'|'not_requested'|null;usage_guide:string[];id:string;product_id:number;name:string;image:string;quantity:number;account:string;total:number;discount:number;variant_id:string;variant_name:string;payment_method:'mock'|'alipay'|'wechat'|'balance';status:'pending'|'paid'|'cancelled';delivery:string|null;created:number};
export type Lottery = {participants:number;product_id:number;prize:string;probability:number;ends_at:number;campaign:string;mock:boolean};
