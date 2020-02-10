#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb  9 21:27:19 2020

@author: vamsi
"""

import pandas as pd
import numpy as np
import wget, sys
from datetime import date,datetime
import os.path
import mibian
import warnings
warnings.filterwarnings("ignore")

out_dir = 'snapshot'
url = 'https://marketdata.theocc.com/series-search?symbolType=U&symbol=TSLA'
fname = os.path.join(out_dir,date.today().strftime("%Y-%m-%d"))

curr_price=748.0
flatvol=85.5
delta = 50

rate=2.0
conSum = pd.DataFrame()

def greek_string(deets, iv):
    #array deets needs [underlyingPrice, strikePrice, interestRate, daysToExpiration]
    c=mibian.BS(deets,iv)
    return([c.callPrice,c.putPrice,c.callDelta,c.putDelta,c.callDelta2,c.putDelta2,
            c.callTheta,c.putTheta,c.callRho,c.putRho,c.vega,c.gamma])

print ('price, vol, delta:', str(sys.argv))
try:
    curr_price=sys.argv[1]
    flatvol=sys.argv[2]
    delta = sys.argv[3]
except:
    print('usage: python oi.py price volatility step')

if os.path.isfile(fname):
    print ("File exists")
else:
    wget.download(url,out=fname)


#Data wragling to clean up the raw file    
df = pd.read_csv(fname, sep='\\t', engine='python',skiprows = 6)
df.drop(columns=['ProductSymbol','C/P','Position Limit'],inplace=True)
df.reset_index(inplace=True)
df.drop(columns=['index'],inplace=True)
df.rename(columns={'Integer':'Strike'}, inplace=True)
df['Strike']=df['Strike']+df['Dec']/1000
df['Expiry']=pd.to_datetime(df[['year','Month','Day']])
#add a day to expiry to prevent zero days to expiry on Fridays
df['Expiry'] = df['Expiry'] + pd.DateOffset(days=1)

df.drop(columns=['Dec','year','Month','Day'],inplace=True)
#Done data wrangling

#retain rows for dates >= today()
df=df[df['Expiry']>=datetime.today()]

#add column for days to expiry
df['DTE'] = df['Expiry']-datetime(datetime.today().year,datetime.today().month,datetime.today().day)
df['DTE'] = df['DTE'].dt.days 

for price in (curr_price-delta,curr_price,curr_price+delta):
    #get all model prices and greeks
    df['Greeks']=df.apply(lambda  x:greek_string([price,x['Strike'],rate,x['DTE']],flatvol), axis=1)
    
    #create columns for the greeks
    df['callPrice'], df['putPrice'],df['callDelta'], df['putDelta'],df['callDelta2'], df['putDelta2'], df['callTheta'], df['putTheta'],df['callRho'], df['putRho'],df['vega'], df['gamma'] = zip(*df.pop('Greeks'))
    
    #call and put hedges in shares
    
    result=df[['Expiry','Call','Put','callDelta','putDelta']]
    result['callHedge']=result['Call']*result['callDelta']*100
    result['putHedge']=result['Put']*result['putDelta']*100
    result['netHedge']=result['callHedge']+result['putHedge']
    sumByExpiry=result[['Expiry','callHedge','putHedge','netHedge']].groupby('Expiry').sum()
    sumByExpiry['Price']=price
    conSum=conSum.append(sumByExpiry)
    
conSum.to_csv(fname+':summary.csv',header=True)

pivtable = pd.pivot_table(conSum,values=['netHedge'],index=['Expiry'], columns=['Price'], aggfunc=np.sum)

print('Hedge impact from a '+str(delta)+' point move')
print("Down:"+"{:,d}".format(int(pivtable.sum(axis=0)[0]-pivtable.sum(axis=0)[1])))
print("Up  :"+"{:,d}".format(int(pivtable.sum(axis=0)[2]-pivtable.sum(axis=0)[1])))


print(pivtable)
