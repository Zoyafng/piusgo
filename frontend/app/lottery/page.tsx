import {serverApi,type Lottery} from '@/lib/api';
import LotteryPage from '@/components/lottery';
export const metadata={title:'指定商品抽奖',robots:{index:false,follow:false}};
export default async function Page(){return <LotteryPage info={await serverApi<Lottery>('/lottery')}/>;}
