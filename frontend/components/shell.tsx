'use client';
import Link from 'next/link';
import {BRAND} from '@/lib/brand';
import {usePathname} from 'next/navigation';
import {useState} from 'react';
import {Gift,Ticket,Handbag,MagnifyingGlass,BookOpen,Megaphone,User,Bell,Headset} from './icons';
const nav = [
  ['/offers/recharge','充值中心',Gift,'promo'],['/offers/newcomer','新人优惠',Ticket,'promo'],
  ['/','购物',Handbag,''],['/orders','查订单',MagnifyingGlass,''],
  ['/guide/tutorial','使用教程',BookOpen,''],['/tickets','提交工单',Ticket,''],
  ['/guide/announcements','公告',Megaphone,''],['/account','个人中心',User,''],
] as const;
export function Brand() {
  return <Link className="brand" href="/" aria-label={`${BRAND.name}首页`}><img src={BRAND.logo} alt="" width={40} height={40}/><span><strong>{BRAND.name}</strong><small>{BRAND.tagline}</small></span></Link>;
}
export function Header() {
  const path = usePathname();
  if(path.startsWith('/admin'))return null;
  return <header className="site-header"><Brand/><nav className="main-nav" aria-label="主导航">{nav.map(([href,label,Icon,style])=><Link key={href} href={href} className={`${style} ${path===href?'active':''}`} aria-current={path===href?'page':undefined}><Icon size={19} weight="regular"/><span>{label}</span></Link>)}</nav><div className="header-actions"><Link className="icon-button" href="/guide/announcements" aria-label="通知"><Bell size={21}/></Link><Link className="icon-button" href="/account" aria-label="我的账号"><User size={21}/></Link></div></header>;
}
export function Footer() {
  const path=usePathname();
  if(path.startsWith('/admin'))return null;
  return <footer className="site-footer"><div><Brand/><nav aria-label="页脚导航"><Link href="/orders">查询订单</Link><Link href="/guide/tutorial">使用教程</Link><Link href="/tickets">提交工单</Link><Link href="/guide/terms">平台说明</Link></nav></div><p>本地演示环境 · 商品、优惠及支付均为模拟数据，不产生真实扣款或充值。</p></footer>;
}
export function Support() {
  const [open,setOpen]=useState(false);
  const path=usePathname();
  if(path.startsWith('/admin'))return null;
  return <aside className="support"><button className="qq-button" aria-label="客服联系方式" aria-expanded={open} onClick={()=>setOpen(!open)}><img src="/assets/qq.png" alt="QQ 客服" width={32} height={32}/></button>{open?<div className="support-popover"><strong>需要帮助？</strong><p>演示环境暂未配置 QQ 客服。请提交工单，我们会保存你的问题。</p><Link href="/tickets" onClick={()=>setOpen(false)}>前往工单中心</Link></div>:null}<Link href="/tickets" className="support-link"><Headset size={23}/><span>在线客服</span></Link></aside>;
}
