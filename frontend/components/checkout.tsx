'use client';
import {useEffect,useState} from 'react';

import {api,ApiError,errorText,money,type Product,type Member,type Order} from '@/lib/api';
import {Wallet} from './icons';
import OrderReceipt,{cacheOrder} from './order-receipt';
export default function Checkout({product:p}:{product:Product}) {
  const [me,setMe]=useState<Member|null>(null),[loaded,setLoaded]=useState(false);
  const [quantity,setQuantity]=useState(1),[variantId,setVariantId]=useState(p.variants[0]?.id||'');
  const [method,setMethod]=useState<'alipay'|'wechat'|'balance'>('alipay');
  const [coupon,setCoupon]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[key,setKey]=useState('');
  const [created,setCreated]=useState<Order|null>(null);
  useEffect(()=>{setKey(crypto.randomUUID());let active=true;api<{user:Member|null}>('/checkout/session').then(m=>{if(active)setMe(m.user);}).catch(e=>{if(active&&!(e instanceof ApiError&&e.status===401))setError(errorText(e));}).finally(()=>{if(active)setLoaded(true);});return()=>{active=false;};},[]);
  const variant=p.variants.find(v=>v.id===variantId)||p.variants[0];
  const price=variant?.price||p.price, stock=variant?.stock??0;
  const eligible=me?.coupons.filter(c=>!c.used&&price*quantity>=c.minimum&&(!c.kind.startsWith('lottery:')||p.id===188))||[];
  const discount=eligible.find(c=>c.id===coupon)?.amount||0;
  function changed(){setKey(crypto.randomUUID());setError('');}
  function changeQuantity(q:number){setQuantity(q);setCoupon('');changed();}
  async function submit(e:React.FormEvent<HTMLFormElement>){
    e.preventDefault();const form=new FormData(e.currentTarget);
    setBusy(true);setError('');
    try{const order=await api<Order>('/orders',{product_id:p.id,variant_id:variantId,quantity,account:me?undefined:form.get('email'),payment_method:method,coupon_id:eligible.some(c=>c.id===coupon)?coupon:null,request_key:key});cacheOrder(order.id);setCreated(order);}
    catch(e){setError(errorText(e));}
    finally{setBusy(false);}
  }
  if(created)return <OrderReceipt initial={created} openPayment/>;
  return <div className="detail-layout"><section className="panel detail-intro"><div className="detail-cover"><img src={p.image} alt={p.name} width={140} height={140}/></div><span className="eyebrow">DIGITAL SERVICE</span><h1>{p.name}</h1><div className="product-tags">{p.tags.map((t,i)=><span key={t} className={`tone-${i%3}`}>{t}</span>)}</div><h2>商品说明</h2><p>选择商品规格、数量与支付方式。登录后使用账户邮箱接收订单信息，下单记录可在「查订单」查看。</p><p>这是演示商品，支付后生成演示卡密，不会向任何第三方平台发起真实充值。</p></section>
  <section className="panel checkout-panel"><h2>{p.name}</h2><div className="product-tags">{p.tags.map((t,i)=><span key={t} className={`tone-${i%3}`}>{t}</span>)}<span className="tone-0">库存 {stock}</span></div><strong className="detail-price">{money(price)}</strong>
  <form className="stack-form" onSubmit={submit}>
    <fieldset className="checkout-fieldset"><legend>商品规格</legend><div className="variant-options">{p.variants.map(v=><label className={`variant-option ${variantId===v.id?'selected':''}`} key={v.id}><input type="radio" name="variant" value={v.id} checked={variantId===v.id} disabled={v.stock===0} onChange={()=>{setVariantId(v.id);setQuantity(1);setCoupon('');changed();}}/><span><b>{v.name}</b><small><strong>{money(v.price)}</strong> · 库存 {v.stock}</small></span></label>)}</div></fieldset>
    {loaded&&!me?<label><span>电子邮箱</span><input name="email" type="email" required maxLength={191} autoComplete="email" placeholder="请输入您的常用邮箱"/><small>用于接收卡密与订单交付信息。</small></label>:null}
    <label><span>购买数量</span><div className="quantity-control"><button type="button" aria-label="减少购买数量" disabled={quantity<=1} onClick={()=>changeQuantity(quantity-1)}>−</button><input aria-label="购买数量" name="quantity" type="number" min={1} max={Math.min(10,stock)} required value={quantity} onChange={e=>changeQuantity(Number(e.target.value))}/><button type="button" aria-label="增加购买数量" disabled={quantity>=Math.min(10,stock)} onClick={()=>changeQuantity(quantity+1)}>+</button></div></label>
    <fieldset className="checkout-fieldset payment-box"><legend>支付方式</legend><div className="payment-options">{(['alipay','wechat','balance'] as const).map(value=><label className={`payment-option ${method===value?'selected':''} ${value==='balance'&&!me?'unavailable':''}`} key={value}><input type="radio" name="payment" value={value} checked={method===value} disabled={value==='balance'&&!me} onChange={()=>{setMethod(value);changed();}}/>{value==='balance'?<Wallet size={24}/>:<img src={`/assets/payment-${value}.png`} alt="" width={24} height={24}/>}<span><b>{value==='alipay'?'支付宝':value==='wechat'?'微信支付':'余额付款'}</b>{value==='balance'?<small>{me?`可用 ${money(me.balance)}`:'需先登录'}</small>:null}</span></label>)}</div><small className="payment-note">当前为模拟支付，不产生真实扣款。</small></fieldset>
    {eligible.length?<label><span>优惠券</span><select value={coupon} onChange={e=>{setCoupon(e.target.value);changed();}}><option value="">不使用优惠券</option>{eligible.map(c=><option key={c.id} value={c.id}>{c.kind==='newcomer'?'新人优惠':'抽奖优惠'} · 减 {money(c.amount)}</option>)}</select></label>:null}
    <div><div className="summary-line"><span>商品总额</span><span>{money(price*quantity)}</span></div><div className="summary-line"><span>优惠金额</span><span>−{money(Math.min(price*quantity,discount))}</span></div><div className="summary-line"><span>应付金额</span><strong>{money(Math.max(0,price*quantity-discount))}</strong></div></div>{error?<p className="error-message" role="alert">{error}</p>:null}<button className="primary wide" disabled={busy||!loaded||stock<1}>{stock<1?'商品暂时缺货':busy?'正在创建订单…':'立即购买'}</button><small>提交订单后进入付款步骤。</small>
  </form></section></div>;
}
