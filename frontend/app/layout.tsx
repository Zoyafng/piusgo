import type {Metadata} from 'next';
import {BRAND} from '@/lib/brand';
import {Header,Footer,Support} from '@/components/shell';
import './globals.css';
const origin=process.env.SITE_URL || 'http://localhost:3000';
export const metadata:Metadata={metadataBase:new URL(origin),title:{default:`${BRAND.name}｜商品中心`,template:`%s｜${BRAND.name}`},description:`${BRAND.tagline}。选择你需要的数字服务。浏览商品、优惠活动，在线下单、查询订单和提交售后工单。`,icons:{icon:BRAND.logo,apple:BRAND.logo},robots:process.env.INDEX_SITE==='true'?{index:true,follow:true}:{index:false,follow:false},openGraph:{siteName:BRAND.name,locale:'zh_CN',type:'website',images:[{url:BRAND.logo,width:1254,height:1254}]}};
export default function Layout({children}:{children:React.ReactNode}) { return <html lang="zh-CN"><body><a className="skip-link" href="#main">跳至主要内容</a><Header/>{children}<Footer/><Support/></body></html>; }
