'use client';
import Link from 'next/link';
import {BRAND} from '@/lib/brand';
import {usePathname} from 'next/navigation';
import {useState,useEffect} from 'react';
import {api} from '@/lib/api';
import dynamic from 'next/dynamic';
const SupportWidget=dynamic(()=>import('./support/widget'),{ssr:false});
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
  if(path.startsWith('/admin')||path==='/support')return null;
  return <header className="site-header"><Brand/><nav className="main-nav" aria-label="主导航">{nav.map(([href,label,Icon,style])=><Link key={href} href={href} className={`${style} ${path===href?'active':''}`} aria-current={path===href?'page':undefined}><Icon size={19} weight="regular"/><span>{label}</span></Link>)}</nav><div className="header-actions"><Link className="icon-button" href="/guide/announcements" aria-label="通知"><Bell size={21}/></Link><Link className="icon-button" href="/account" aria-label="我的账号"><User size={21}/></Link></div></header>;
}
export function Footer() {
  const path=usePathname();
  if(path.startsWith('/admin')||path==='/support')return null;
  return <footer className="site-footer"><div><Brand/><nav aria-label="页脚导航"><Link href="/orders">查询订单</Link><Link href="/guide/tutorial">使用教程</Link><Link href="/tickets">提交工单</Link><Link href="/guide/terms">平台说明</Link></nav></div><p>本地演示环境 · 商品、优惠及支付均为模拟数据，不产生真实扣款或充值。</p></footer>;
}
export function Support() {
  const [open,setOpen]=useState(false),[chat,setChat]=useState(false),[visited,setVisited]=useState(false),[unread,setUnread]=useState(0);
  const path=usePathname();
  useEffect(()=>{if(visited||path.startsWith('/admin')||path==='/support')return;let alive=true,version=0;async function check(){const attempt=++version;try{const result=await api<{unread:number}>('/support/unread');if(alive&&version===attempt)setUnread(result.unread);}catch{}}const changed=()=>{version++;setUnread(0);void check();};void check();const timer=setInterval(()=>{if(!document.hidden)void check();},20000);const channel=typeof BroadcastChannel!=='undefined'?new BroadcastChannel('piusgo-auth'):null;if(channel)channel.onmessage=changed;window.addEventListener('piusgo-auth-changed',changed);window.addEventListener('focus',check);return()=>{alive=false;clearInterval(timer);channel?.close();window.removeEventListener('piusgo-auth-changed',changed);window.removeEventListener('focus',check);};},[visited,path]);
  if(path.startsWith('/admin')||path==='/support')return null;
  return <><aside className="support"><button className="qq-button" aria-label="客服联系方式" aria-expanded={open} onClick={()=>setOpen(!open)}><img src="/assets/qq.png" alt="QQ 客服" width={32} height={32}/></button>{open?<div className="support-popover"><strong>需要帮助？</strong><p>可以通过在线客服留言，或提交工单。</p><Link href="/tickets" onClick={()=>setOpen(false)}>前往工单中心</Link></div>:null}<button type="button" className="support-link" aria-controls="support-window" aria-expanded={chat} aria-label={unread?`在线客服，${unread} 条未读消息`:'打开在线客服'} onClick={()=>{setVisited(true);setChat(!chat);setOpen(false);}}><Headset size={23}/><span>在线客服</span>{unread>0?<b className="support-unread">{unread>99?'99+':unread}</b>:null}</button></aside>{visited?<SupportWidget open={chat} close={()=>setChat(false)} onUnread={setUnread}/>:null}</>;
}
