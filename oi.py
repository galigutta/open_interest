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
import mibian,boto3, requests
from yahoo_fin.stock_info import get_quote_table
import warnings,copy
import pandas_market_calendars as mcal
warnings.filterwarnings("ignore")

out_dir = 'snapshot'
#changing to the new production data
#url = 'https://www.theocc.com/webapps/series-search?symbolType=U&symbol=TSLA'
url = 'https://marketdata.theocc.com/series-search?symbolType=U&symbol=TSLA'
datestr=date.today().strftime("%Y-%m-%d")
fname = os.path.join(out_dir,datestr)
s3 = boto3.client('s3')
err_msg='\n'

curr_price=770.0
flatvol=85.5
delta = 100
deltas=[-100,-50,-20,0,20,50,100]
nyse = mcal.get_calendar('NYSE')
rate=2.0
conSum = pd.DataFrame()


def greek_string(deets, iv):
    #array deets needs [underlyingPrice, strikePrice, interestRate, daysToExpiration]
    c=mibian.BS(deets,iv)
    return([c.callPrice,c.putPrice,c.callDelta,c.putDelta,c.callDelta2,c.putDelta2,
            c.callTheta,c.putTheta,c.callRho,c.putRho,c.vega,c.gamma])

try:
    yq = get_quote_table("tsla")
    curr_price = yq['Quote Price']
    now = datetime.now()
    day_volume = 0
    if (now > now.replace(hour=16) and nyse.valid_days(start_date=datestr, end_date=datestr).size==1):
        day_volume = yq['Volume']
except:
    err_msg = err_msg+'unable to get price from yahoo & yahoo_fin, defaulting price\n'
    
try:
    #scrape volatility
    url_vol = 'https://www.ivolatility.com/options.j?ticker=tsla'
    html = requests.get(url_vol).content
    df_list = pd.read_html(html)
    flatvol = float(df_list[4][1][8].strip('%'))
except:
    err_msg = err_msg+'unable to get vol from ivolatility.com, defaulting vol\n'


    
try:
    curr_price=float(sys.argv[1])
    flatvol=float(sys.argv[2])
    delta = float(sys.argv[3])
except:
    if len(sys.argv)>1:
        print('usage: python oi.py price volatility step')

if os.path.isfile(fname):
    print (" ")
else:
    print ("Downloading open interest file")
    wget.download(url,out=fname)

print ('Using '+str(curr_price)+' price, '+str(flatvol)+' imp vol, '+str(delta)+' point move:')
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

    
print ("Copying file to S3/tsla-oi")
df['Date']=datestr
df.to_csv(fname,header=True,index=False)
with open(fname, "rb") as f:
    s3.upload_fileobj(f, "tsla-oi",'%s/%s' % ('snapshot',datestr+'.csv'))
df.drop(columns=['Date'],inplace=True)
#done copying file to S3

#retain rows for dates >= today()
df=df[df['Expiry']>=datetime.today()]

#add column for days to expiry
df['DTE'] = df['Expiry']-datetime(datetime.today().year,datetime.today().month,datetime.today().day)
df['DTE'] = df['DTE'].dt.days 

for shocks in (deltas):
    price = shocks+curr_price
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
    
# The 2 lines below to show impact by expiry. Useful later if the hedge impact needs to be haircut
conSum.to_csv(fname+'-summary.csv',header=True)

with open(fname+'-summary.csv', "rb") as f:
    s3.upload_fileobj(f, "tsla-oi",'%s/%s' % ('summary',datestr+'-summary.csv'))
    
#round prices for better display
pivtable_expiry = pd.pivot_table(conSum.round(1),values=['netHedge'],index=['Expiry'], columns=['Price'], aggfunc=np.sum)

#sumarize for consumption
pivtable = pd.pivot_table(conSum,values=['netHedge'], columns=['Price'], aggfunc=np.sum)
#calculate change in hege need
to_subtract = copy.copy(pivtable[curr_price])
for shocks in (deltas):
    price = shocks+curr_price
    if (shocks!=0):
        pivtable[price]=pivtable[price]-to_subtract
pivtable.columns=deltas

#Merge columns and output
summary_output = pd.DataFrame()
summary_output['Date']=[date.today().strftime("%Y-%m-%d")]
summary_output['Price']=[curr_price]
summary_output['Volume']=[day_volume]
summary_output['IV']=[flatvol]
summary_output['key']=[0]
pivtable['key']=[0]
summary_output=pd.merge(summary_output,pivtable,on='key')
summary_output.drop(columns=['key'],inplace=True)

print(summary_output)

#adding code for s3 handling and static publishing


s3.download_file('tsla-oi', 'index.csv', 'index.csv')

#read it into a dataframe
index_csv=pd.read_csv('index.csv')

#append the summary output row
index_csv.columns = summary_output.columns
summary_output = summary_output.append(index_csv)
#drop dpulicates
summary_output.drop_duplicates(subset=None, keep='first', inplace=True)

# summary_output.fillna(value=0).to_html('index.html',index=False,float_format="{0:,.0f}".format)
# fn=open("index.html","a")
# fn.write("\nLast updated at: "+datetime.today().strftime("%Y-%m-%d %H:%M:%S")+ " EST")
# fn.write("<br>"+requests.get('https://api.ipify.org').text)
# fn.write(err_msg)
# fn.close()
with open("index.html", 'w') as fn:
    fn.write("By @generalenthu")
    fn.write("<br>Last updated at: "+datetime.today().strftime("%Y-%m-%d %H:%M:%S")+ " EST")
    fn.write("<br>Sensitivity ranges set to be +/-100 points wide post split. Previously +/-200")
    fn.close()
with open("index.html", 'a') as fn:
    fn.write('<br>'+pivtable_expiry.fillna(value=0).to_html(float_format="{0:,.0f}".format))
    fn.write('<br>'+summary_output.fillna(value=0).to_html(index=False,float_format="{0:,.0f}".format))
    fn.write("<br>"+requests.get('https://api.ipify.org').text)
    fn.write(err_msg)
    fn.close()

summary_output.to_csv('so.csv',index=False)


#copyback the files to s3

with open("index.html", "rb") as f:
    s3.upload_fileobj(f, "tsla-oi", "index.html",ExtraArgs={'ContentType':'text/html'})

with open("so.csv", "rb") as f:
    s3.upload_fileobj(f, "tsla-oi", "index.csv")

