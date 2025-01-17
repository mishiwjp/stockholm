#coding:utf-8
import requests
import json
import datetime
import timeit
import time
import io
import os
import csv
import re
from pymongo import MongoClient
from multiprocessing.dummy import Pool as ThreadPool
from functools import partial
import tushare as ts
# import baostock as bs
import pandas as pd

class Stockholm(object):

    def __init__(self, args):
        ## flag of if need to reload all stock data
        self.reload_data = args.reload_data
        ## flag of if need to reprocess stock data
        self.process_data = args.process_data
        ## flag of if need to run the dev mode
        self.single_stock = args.run_single
        ## flag of if need to generate portfolio
        self.gen_portfolio = args.gen_portfolio
        ## type of output file json/csv or both
        self.output_type = args.output_type
        ## charset of output file utf-8/gbk
        self.charset = args.charset
        ## portfolio testing date range(# of days)
        self.test_date_range = args.test_date_range
        ## stock data loading start date(e.g. 2014-09-14)
        self.start_date = args.start_date
        ## stock data loading end date
        self.end_date = args.end_date
        ## portfolio generating target date
        self.target_date = args.target_date
        ## thread number
        self.thread = args.thread
        ## data file store path
        if(args.store_path == 'USER_HOME/tmp/stockholm_export'):
            self.export_folder = os.path.expanduser('~') + '/tmp/stockholm_export'
        else:
            self.export_folder = args.store_path
        ## portfolio testing file path
        self.testfile_path = args.testfile_path
        ## porfit buy file path
        self.buyfile_path = args.buyfile_path
        ## profit sell file path
        self.sellfile_path = args.sellfile_path
        ## methods for back testing
        self.methods = args.methods

        ## for getting quote symbols
        self.all_quotes_url = 'http://money.finance.sina.com.cn/d/api/openapi_proxy.php'
        ## for loading quote data
        self.yql_url = 'http://query.yahooapis.com/v1/public/yql'
        ## export file name
        self.export_file_name = 'stockholm_export'

        self.index_array = ['000001.SS', '399001.SZ']
        self.sh000001 = {'Symbol': '000001.SS', 'Name': '上证指数'}
        self.sz399001 = {'Symbol': '399001.SZ', 'Name': '深证成指'}
        self.sh000300 = {'Symbol': '000300.SS', 'Name': '沪深300'}
        ## self.sz399005 = {'Symbol': '399005.SZ', 'Name': '中小板指'}
        ## self.sz399006 = {'Symbol': '399006.SZ', 'Name': '创业板指'}

        ## mongodb info
        self.mongo_url = 'localhost'
        self.mongo_port = 27017
        self.database_name = args.db_name
        self.collection_name = 'testing_method'
        
    def get_columns(self, quote):
        columns = []
        if(quote is not None):
            for key in quote.keys():
                if(key == 'Data'):
                    for data_key in quote['Data'][-1]:
                        columns.append("data." + data_key)
                else:
                    columns.append(key)
            columns.sort()
        return columns

    def get_profit_rate(self, price1, price2):
        if(price1 == 0):
            return None
        else:
            return round((price2-price1)/price1, 5)

    def get_MA(self, number_array):
        total = 0
        n = 0
        for num in number_array:
            if num is not None and num != 0:
                n += 1
                total += num
        return round(total/n, 3)

    def convert_value_check(self, exp):
        val = exp.replace('day', 'quote[\'Data\']').replace('(0)', '(-0)')
        val = re.sub(r'\(((-)?\d+)\)', r'[target_idx\g<1>]', val)
        val = re.sub(r'\.\{((-)?\w+)\}', r"['\g<1>']", val)
        return val

    def convert_null_check(self, exp):
        p = re.compile('\((-)?\d+...\w+\}')
        iterator = p.finditer(exp.replace('(0)', '(-0)'))
        array = []
        for match in iterator:
            v = 'quote[\'Data\']' + match.group()
            v = re.sub(r'\(((-)?\d+)\)', r'[target_idx\g<1>]', v)
            v = re.sub(r'\.\{((-)?\w+)\}', r"['\g<1>']", v)
            v += ' is not None'
            array.append(v)
        val = ' and '.join(array)
        return val

    def usedbcol(self,collection):
        client = MongoClient(self.mongo_url, self.mongo_port)
        db = client[self.database_name]
        return db[collection]

    def db_operate(self,data,collection,operation,index=''):
        client = MongoClient(self.mongo_url, self.mongo_port)
        db = client[self.database_name]
        if operation=='replace':
            db[collection].drop()
            if type(data) == list:
                db[collection].insert_many(data)
            else:
                db[collection].insert_one(data)
            if index:
                indexs = db[collection].index_information()
                if '_id_' in indexs:
                    db[collection].create_index([(index, 1)], unique=True)

    class KDJ():
        def _avg(self, array):
            length = len(array)
            return sum(array)/length
        
        def _getMA(self, values, window):
            array = []
            x = window
            while x <= len(values):
                curmb = 50
                if(x-window == 0):
                    curmb = self._avg(values[x-window:x])
                else:
                    curmb = (array[-1]*2+values[x-1])/3
                array.append(round(curmb,3))
                x += 1
            return array
        
        def _getRSV(self, arrays):
            rsv = []
            x = 9
            while x <= len(arrays):
                high = max(map(lambda x: x['High'], arrays[x-9:x]))
                low = min(map(lambda x: x['Low'], arrays[x-9:x]))
                close = arrays[x-1]['Close']
                rsv.append((close-low)/(high-low)*100)
                t = arrays[x-1]['Date']
                x += 1
            return rsv
        
        def getKDJ(self, quote_data):
            if(len(quote_data) > 12):
                rsv = self._getRSV(quote_data)
                k = self._getMA(rsv,3)
                d = self._getMA(k,3)
                j = list(map(lambda x: round(3*x[0]-2*x[1],3), zip(k[2:], d)))
                
                for idx, data in enumerate(quote_data[0:12]):
                    data['KDJ_K'] = None
                    data['KDJ_D'] = None
                    data['KDJ_J'] = None
                for idx, data in enumerate(quote_data[12:]):
                    data['KDJ_K'] = k[2:][idx]
                    data['KDJ_D'] = d[idx]
                    if(j[idx] > 100):
                        data['KDJ_J'] = 100
                    elif(j[idx] < 0):
                        data['KDJ_J'] = 0
                    else:
                        data['KDJ_J'] = j[idx]
                
            return quote_data

    class CurveMatch():
        def match_Peak(self,quote_data):
            x = increment = 19
            while x < len(quote_data):
                high = max(map(lambda x: x['High'], quote_data[x-increment:x]))
                high_index = list(map(lambda x: x['High'], quote_data[x-increment:x])).index(high)+x-increment
                low = min(map(lambda x: x['Low'], quote_data[x-increment:x]))
                low_index = list(map(lambda x: x['Low'], quote_data[x-increment:x])).index(low)+x-increment
                if(low_index<high_index):
                    condition = []
                    # 累计涨幅超过10%,最高点和最低点间隔超过2，当前和最高点间隔超过2
                    condition.append(high-low>0.1*low and high_index-low_index>1 and x-high_index>1)
                    red_count = 0
                    green_count = 0
                    for data in quote_data[low_index:high_index+1]:
                        if data['Close']-data['Open']>0:
                            red_count += 1
                    # 上升阶段超过60%时间是红的
                    condition.append((red_count)/(high_index-low_index+1)>0.6)
                    for data in quote_data[high_index+1:x]:
                        if data['Close']-data['Open']<=0:
                            green_count += 1
                    # 回调阶段超过50%时间是绿的
                    condition.append(x>high_index+1 and (green_count)/len(quote_data[high_index+1:x])>0.5)
                    # 买入当日最高涨幅超过4%
                    condition.append((quote_data[x]['High']-quote_data[x-1]['Close'])/quote_data[x-1]['Close']>0.04)
                    # 前一天涨幅不超过5%
                    condition.append((quote_data[x-1]['Close']-quote_data[x-1]['Open'])/quote_data[x-1]['Open']<0.05)
                else:
                   condition = [False]
                quote_data[x]['CurveMatch'] = quote_data[x].get('CurveMatch',[])
                if sum(condition)==len(condition):
                    quote_data[x]['CurveMatch'].append('peak')
                else:
                    if 'peak' in quote_data[x]['CurveMatch']:
                       quote_data[x]['CurveMatch'].remove('peak') 
                if(quote_data[x]['Date']=='2022-04-27' and quote_data[x]['High']==24.44):
                    print(condition)
                    print(quote_data[x])
                # if(quote_data[x].get('CurveMatch')):
                #     print(quote_data[x])
                # if(quote_data[x]['Date']=='2021-11-09'):
                #     print(condition[0])
                #     print(condition[1])
                #     print(condition[2])
                #     print(condition[3])

                x += 1
            return quote_data

        def match_all_curve(self,quote_data):
            self.match_Peak(quote_data)

    def load_all_quote_symbol(self):
        print("load_all_quote_symbol start..." + "\n")
        
        start = timeit.default_timer()

        all_quotes = []
        
        all_quotes.append(self.sh000001)
        all_quotes.append(self.sz399001)
        all_quotes.append(self.sh000300)
        ## all_quotes.append(self.sz399005)
        ## all_quotes.append(self.sz399006)
        
        try:
            count = 1
            while (count < 100):
                para_val = '[["hq","hs_a","",0,' + str(count) + ',500]]'
                r_params = {'__s': para_val}
                r = requests.get(self.all_quotes_url, params=r_params)
                if(len(r.json()[0]['items']) == 0):
                    break
                for item in r.json()[0]['items']:
                    quote = {}
                    code = item[0]
                    name = item[2]
                    ## convert quote code
                    if(code.find('sh') > -1):
                        code = code[2:] + '.SS'
                    elif(code.find('sz') > -1):
                        code = code[2:] + '.SZ'
                    ## convert quote code end
                    quote['Symbol'] = code
                    quote['Name'] = name
                    all_quotes.append(quote)
                count += 1
        except Exception as e:
            print("Error: Failed to load all stock symbol..." + "\n")
            print(e)
        
        print("load_all_quote_symbol end... time cost: " + str(round(timeit.default_timer() - start)) + "s" + "\n")
        return all_quotes

    def load_quote_info(self, quote, is_retry):
        print("load_quote_info start..." + "\n")
        
        start = timeit.default_timer()

        if(quote is not None and quote['Symbol'] is not None):
            yquery = 'select * from yahoo.finance.quotes where symbol = "' + quote['Symbol'].lower() + '"'
            r_params = {'q': yquery, 'format': 'json', 'env': 'http://datatables.org/alltables.env'}
            r = requests.get(self.yql_url, params=r_params)
            ## print(r.url)
            ## print(r.text)
            rjson = r.json()
            try:
                quote_info = rjson['query']['results']['quote']
                quote['LastTradeDate'] = quote_info['LastTradeDate']
                quote['LastTradePrice'] = quote_info['LastTradePriceOnly']
                quote['PreviousClose'] = quote_info['PreviousClose']
                quote['Open'] = quote_info['Open']
                quote['DaysLow'] = quote_info['DaysLow']
                quote['DaysHigh'] = quote_info['DaysHigh']
                quote['Change'] = quote_info['Change']
                quote['ChangeinPercent'] = quote_info['ChangeinPercent']
                quote['Volume'] = quote_info['Volume']
                quote['MarketCap'] = quote_info['MarketCapitalization']
                quote['StockExchange'] = quote_info['StockExchange']
                
            except Exception as e:
                print("Error: Failed to load stock info... " + quote['Symbol'] + "/" + quote['Name'] + "\n")
                print(e + "\n")
                if(not is_retry):
                    time.sleep(1)
                    load_quote_info(quote, True) ## retry once for network issue
            
        ## print(quote)
        print("load_quote_info end... time cost: " + str(round(timeit.default_timer() - start)) + "s" + "\n")
        return quote

    def load_all_quote_info(self, all_quotes):
        print("load_all_quote_info start...")
        
        start = timeit.default_timer()
        for idx, quote in enumerate(all_quotes):
            print("#" + str(idx + 1))
            load_quote_info(quote, False)

        print("load_all_quote_info end... time cost: " + str(round(timeit.default_timer() - start)) + "s")
        return all_quotes

    def load_quote_data(self, quote, start_date, end_date, is_retry, counter):
        ## print("load_quote_data start..." + "\n")
        start = timeit.default_timer()
        if(quote is not None and quote['Symbol'] is not None):
            if True:
                try:
                    # open high close low volume price_change p_change ma5 ma10 ma20 v_ma5 v_ma10 v_ma20
                    df = ts.get_hist_data(quote['Symbol'][0:6],start=start_date,end=end_date)
                    rjson = json.loads(df.to_json())
                    dates = rjson["open"].keys()
                    temp_data = []
                    for date in dates:
                        # print(date)
                        d = {'Symbol': quote['Symbol']}
                        d['Date'] = date
                        d['Open'] = rjson["open"][date]
                        d['Close'] = rjson["close"][date]
                        d['High'] = rjson["high"][date]
                        d['Low'] = rjson["low"][date]
                        d['Volume'] = rjson["volume"][date]
                        d['Price_Change'] =rjson["price_change"][date]
                        d['P_Change'] = rjson["p_change"][date]
                        d['MA_5'] = rjson["ma5"][date]
                        d['MA_10'] = rjson["ma10"][date]
                        d['MA_20'] = rjson["ma20"][date]
                        d['V_MA_5'] = rjson["v_ma5"][date]
                        d['V_MA_10'] = rjson["v_ma10"][date]
                        d['V_MA_20'] = rjson["v_ma20"][date]
                        d['Turn_Over'] = rjson["turnover"][date]
                        temp_data.append(d)
                    temp_data.reverse()
                    quote['Data'] = temp_data
                    if(not is_retry):
                        counter.append(1)          
                except:
                    print("Error: Failed to load stock data... " + quote['Symbol'] + "/" + quote['Name'] + "\n")
                    if(not is_retry):
                        time.sleep(2)
                        self.load_quote_data(quote, start_date, end_date, True, counter) ## retry once for network issue
            else:
                rs = bs.query_history_k_data_plus(quote['Symbol'][-2:]+'.'+quote['Symbol'][0:6],
                    "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,isST",
                    start_date=start_date, end_date=end_date,
                    frequency="d", adjustflag="3")
                data_list = []
                while (rs.error_code == '0') & rs.next():
                    data_list.append(rs.get_row_data())
                result = pd.DataFrame(data_list,columns=rs.fields)
                rjson = json.loads(result.to_json(orient='records'))
                temp_data = []
                for item in rjson:
                    d = {'Symbol': item['code']}
                    d['Date'] = item['date']
                    d['Open'] = item['open']
                    d['Close'] = item['close']
                    d['High'] = item['high']
                    d['Low'] = item['low']
                    d['Volume'] = item['volume']
                    d['Price_Change'] =''
                    d['P_Change'] = item['pctChg']
                    d['MA_5'] = ''
                    d['MA_10'] = ''
                    d['MA_20'] = ''
                    d['V_MA_5'] = ''
                    d['V_MA_10'] = ''
                    d['V_MA_20'] = ''
                    temp_data.append(d)
                # temp_data.reverse()
                quote['Data'] = temp_data
                if(not is_retry):
                    counter.append(1)          
            print("load_quote_data " + quote['Symbol'] + "/" + quote['Name'] + " end..." + "\n")
            ## print("time cost: " + str(round(timeit.default_timer() - start)) + "s." + "\n")
            ## print("total count: " + str(len(counter)) + "\n")
        return quote

    def load_all_quote_data(self, all_quotes, start_date, end_date):
        print("load_all_quote_data start..." + "\n")
        
        start = timeit.default_timer()

        counter = []
        # lg = bs.login()
        mapfunc = partial(self.load_quote_data, start_date=start_date, end_date=end_date, is_retry=False, counter=counter)
        pool = ThreadPool(self.thread)
        pool.map(mapfunc, all_quotes) ## multi-threads executing
        pool.close() 
        pool.join()
        # bs.logout()
        print("load_all_quote_data end... time cost: " + str(round(timeit.default_timer() - start)) + "s" + "\n")
        return all_quotes

    def data_process(self, all_quotes):
        print("data_process start..." + "\n")
        cm = self.CurveMatch()
        kdj = self.KDJ()
        start = timeit.default_timer()
        
        for quote in all_quotes:

            if(quote['Symbol'].startswith('300') or quote['Symbol'].startswith('301')):
                quote['Type'] = '创业板'
            elif(quote['Symbol'].startswith('688')):
                quote['Type'] = '科创板'
            else:
                quote['Type'] = '主板'
            
            if('Data' in quote):
                try:
                    temp_data = []
                    for quote_data in quote['Data']:
                        if(quote_data['Volume'] != '000' or quote_data['Symbol'] in self.index_array):
                            d = {}
                            d['Open'] = float(quote_data['Open'])
                            ## d['Adj_Close'] = float(quote_data['Adj_Close'])
                            d['Close'] = float(quote_data['Close'])
                            d['High'] = float(quote_data['High'])
                            d['Low'] = float(quote_data['Low'])
                            d['Volume'] = int(quote_data['Volume'])
                            d['Date'] = quote_data['Date']
                            d['MA_5'] = float(quote_data['MA_5'])
                            d['MA_10'] = float(quote_data['MA_10'])
                            d['MA_20'] = float(quote_data['MA_20'])
                            d['V_MA_5'] = float(quote_data['V_MA_5'])
                            d['V_MA_10'] = float(quote_data['V_MA_10'])
                            d['V_MA_20'] = float(quote_data['V_MA_20'])
                            d['P_Change'] = float(quote_data['P_Change'])
                            d['Turn_Over'] = float(quote_data['Turn_Over'])
                            d['Type'] = quote['Type']
                            d['Symbol'] = quote_data['Symbol']
                            d['Name'] = quote['Name']
                            temp_data.append(d)
                    quote['Data'] = temp_data
                except KeyError as e:
                    print("Data Process: Key Error")
                    print(e)
                    print(quote)

        ## calculate Change / 5 10 20 30 Day MA
        for quote in all_quotes:
            if('Data' in quote):
                try:
                    for i, quote_data in enumerate(quote['Data']):
                        if(i > 0):
                            quote_data['Change'] = self.get_profit_rate(quote['Data'][i-1]['Close'], quote_data['Close'])
                            quote_data['Vol_Change'] = self.get_profit_rate(quote['Data'][i-1]['Volume'], quote_data['Volume'])                        
                        else:
                            quote_data['Change'] = None
                            quote_data['Vol_Change'] = None
                            
                    # last_5_array = []
                    # last_10_array = []
                    # last_20_array = []
                    # last_30_array = []
                    # for i, quote_data in enumerate(quote['Data']):
                    #     last_5_array.append(quote_data['Close'])
                    #     last_10_array.append(quote_data['Close'])
                    #     last_20_array.append(quote_data['Close'])
                    #     last_30_array.append(quote_data['Close'])
                    #     quote_data['MA_5'] = None
                    #     quote_data['MA_10'] = None
                    #     quote_data['MA_20'] = None
                    #     quote_data['MA_30'] = None
                        
                    #     if(i < 4):
                    #         continue
                    #     if(len(last_5_array) == 5):
                    #         last_5_array.pop(0)
                    #     quote_data['MA_5'] = self.get_MA(last_5_array)
                        
                    #     if(i < 9):
                    #         continue
                    #     if(len(last_10_array) == 10):
                    #         last_10_array.pop(0)
                    #     quote_data['MA_10'] = self.get_MA(last_10_array)
                        
                    #     if(i < 19):
                    #         continue
                    #     if(len(last_20_array) == 20):
                    #         last_20_array.pop(0)
                    #     quote_data['MA_20'] = self.get_MA(last_20_array)
                        
                    #     if(i < 29):
                    #         continue
                    #     if(len(last_30_array) == 30):
                    #         last_30_array.pop(0)
                    #     quote_data['MA_30'] = self.get_MA(last_30_array)
                        
                        
                except KeyError as e:
                    print("Key Error")
                    print(e)
                    print(quote)

        ## calculate KDJ,CurveMatch
        for quote in all_quotes:
            if('Data' in quote):
                try:
                    kdj.getKDJ(quote['Data'])
                    cm.match_all_curve(quote['Data'])
                except KeyError as e:
                    print("Key Error")
                    print(e)
                    print(quote)

        print("data_process end... time cost: " + str(round(timeit.default_timer() - start)) + "s" + "\n")

    def data_export(self, all_quotes, export_type_array, file_name):
        
        start = timeit.default_timer()
        directory = self.export_folder
        if(file_name is None):
            file_name = self.export_file_name
        if not os.path.exists(directory):
            os.makedirs(directory)

        if(all_quotes is None or len(all_quotes) == 0):
            print("no data to export...\n")
        
        if('json' in export_type_array):
            print("start export to JSON file...\n")
            f = io.open(directory + '/' + file_name + '.json', 'w', encoding=self.charset)
            json.dump(all_quotes, f, ensure_ascii=False)
            
        if('csv' in export_type_array):
            print("start export to CSV file...\n")
            columns = []
            if(all_quotes is not None and len(all_quotes) > 0):
                columns = self.get_columns(all_quotes[0])
            writer = csv.writer(open(directory + '/' + file_name + '.csv', 'w', encoding=self.charset))
            writer.writerow(columns)

            for quote in all_quotes:
                if('Data' in quote):
                    for quote_data in quote['Data']:
                        try:
                            line = []
                            for column in columns:
                                if(column.find('data.') > -1):
                                    if(column[5:] in quote_data):
                                        line.append(quote_data[column[5:]])
                                else:
                                    line.append(quote[column])
                            writer.writerow(line)
                        except Exception as e:
                            print(e)
                            print("write csv error: " + quote)
            
        if('mongo' in export_type_array):
            print("start export to MongoDB...\n")
            
        print("export is complete... time cost: " + str(round(timeit.default_timer() - start)) + "s" + "\n")

    def file_data_load(self):
        print("file_data_load start..." + "\n")
        
        start = timeit.default_timer()
        directory = self.export_folder
        file_name = self.export_file_name
        
        all_quotes_data = []
        f = io.open(directory + '/' + file_name + '.json', 'r', encoding='utf-8')
        json_str = f.readline()
        all_quotes_data = json.loads(json_str)
        
        print("file_data_load end... time cost: " + str(round(timeit.default_timer() - start)) + "s" + "\n")
        return all_quotes_data

    def check_date(self, all_quotes, date):    
        is_date_valid = False
        if len(all_quotes) == 1:
            for quote_data in all_quotes[0]['Data']:
                if(quote_data['Date'] == date):
                    is_date_valid = True
        else:
            for quote in all_quotes:
                if(quote['Symbol'] in self.index_array and 'Data' in quote):
                    for quote_data in quote['Data']:    
                        if(quote_data['Date'] == date):
                            is_date_valid = True
        if not is_date_valid:
            print(date + " is not valid...\n")
        return is_date_valid

    def quote_pick(self, all_quotes, target_date, methods):
        print("quote_pick start..." + "\n")
        start = timeit.default_timer()

        results = []
        data_issue_count = 0
        
        for quote in all_quotes:
            try:
                if(quote['Symbol'] in self.index_array):
                    results.append(quote)
                    continue
                
                target_idx = None
                for idx, quote_data in enumerate(quote['Data']):
                    if(quote_data['Date'] == target_date):
                        target_idx = idx
                if(target_idx is None):
                    ## print(quote['Name'] + " data is not available at this date..." + "\n")
                    data_issue_count+=1
                    continue
                
                ## pick logic ##
                valid = False
                for method in methods:
                    ## print(method['name'])
                    ## null_check = eval(method['null_check'])
                    try:
                        value_check = eval(method['value_check'])
                        if(value_check):
                            quote['Method'] = method['name']
                            results.append(quote)
                            valid = True
                            break
                    except:
                        valid = False
                if(valid):
                    continue
                                    
                ## pick logic end ##
                
            except KeyError as e:
                ## print("KeyError: " + quote['Name'] + " data is not available..." + "\n")
                data_issue_count+=1
                
        print("quote_pick end... time cost: " + str(round(timeit.default_timer() - start)) + "s" + "\n")
        print(str(data_issue_count) + " quotes of data is not available...\n")
        return results

    def profit_test(self, selected_quotes, target_date):
        print("profit_test start..." + "\n")
        buypath = self.buyfile_path
        sellpath = self.sellfile_path
        start = timeit.default_timer()
        sell_points = []
        buy_points = []
        results = []
        INDEX = None
        INDEX_idx = 0

        if os.path.exists(sellpath):
            f = io.open(sellpath, 'r', encoding='utf-8')
            for line in f:
                if(line.startswith('##') or len(line.strip()) == 0):
                    continue
                line = line.strip().strip('\n')
                name = line[line.find('[')+1:line.find(']:')]
                value = line[line.find(']:')+2:]
                sell_point = {'name': name, 'value_check': self.convert_value_check(value)}
                sell_points.append(sell_point)
        if os.path.exists(buypath):
            f = io.open(buypath, 'r', encoding='utf-8')
            for line in f:
                if(line.startswith('##') or len(line.strip()) == 0):
                    continue
                line = line.strip().strip('\n')
                name = line[line.find('[')+1:line.find(']:')]
                value = line[line.find(']:')+2:]
                buy_point = {'name': name, 'value_check': self.convert_value_check(value)}
                buy_points.append(buy_point)
        for quote in selected_quotes:
            if(quote['Symbol'] == self.sh000300['Symbol']):
                INDEX = quote
                for idx, quote_data in enumerate(quote['Data']):
                    if(quote_data['Date'] == target_date):
                        INDEX_idx = idx
                break
        
        for quote in selected_quotes:
            target_idx = None
            
            if(quote['Symbol'] in self.index_array):
                continue
            
            for idx, quote_data in enumerate(quote['Data']):
                if(quote_data['Date'] == target_date):
                    target_idx = idx
            if(target_idx is None):
                print(quote['Name'] + " data is not available for testing..." + "\n")
                continue
            
            test = {}
            test['Name'] = quote['Name']
            test['Symbol'] = quote['Symbol']
            test['Method'] = quote['Method']
            test['Type'] = quote['Type']
            if('KDJ_K' in quote['Data'][target_idx]):
                test['KDJ_K'] = quote['Data'][target_idx]['KDJ_K']
                test['KDJ_D'] = quote['Data'][target_idx]['KDJ_D']
                test['KDJ_J'] = quote['Data'][target_idx]['KDJ_J']
            test['Close'] = quote['Data'][target_idx]['Close']
            test['Change'] = quote['Data'][target_idx]['Change']
            test['Vol_Change'] = quote['Data'][target_idx]['Vol_Change']
            test['MA_5'] = quote['Data'][target_idx]['MA_5']
            test['MA_10'] = quote['Data'][target_idx]['MA_10']
            test['MA_20'] = quote['Data'][target_idx]['MA_20']
            # test['MA_30'] = quote['Data'][target_idx]['MA_30']
            test['CurveMatch'] = quote['Data'][target_idx].get('CurveMatch',[])
            test['Data'] = [{}]
            custom_buy_point = 0
            custom_sell_point = 0
            for i in range(1,11):
                if(target_idx+i >= len(quote['Data'])):
                    print(quote['Name'] + " data is not available for " + str(i) + " day testing..." + "\n")
                    if custom_sell_point==0:
                        # print(4444)
                        custom_sell_point = quote['Data'][target_idx+i-1]['Open']
                    break
                if(custom_sell_point == 0 and custom_buy_point!=0):
                    for sell_point in sell_points:
                        value_check = eval(sell_point['value_check'])
                        if(quote['Data'][target_idx+i-1]['Low']<=value_check<=quote['Data'][target_idx+i-1]['High']):
                            # print (555)
                            custom_sell_point = value_check
                        else:
                            if i==2 and (custom_buy_point<=value_check<=quote['Data'][target_idx+i-2]['Close'] or quote['Data'][target_idx+i-2]['Close']<=value_check<=custom_buy_point):
                                # print(6666)
                                custom_sell_point = quote['Data'][target_idx+i-1]['Open']
                            if i==11:
                                custom_sell_point = quote['Data'][target_idx+i-1]['Close']
                if(custom_buy_point == 0):
                    for buy_point in buy_points:
                        value_check = eval(buy_point['value_check'])
                        if(quote['Data'][target_idx+i-1]['Low']<=value_check<=quote['Data'][target_idx+i-1]['High']):
                            custom_buy_point = value_check
                day2day_profit = self.get_profit_rate(quote['Data'][target_idx]['Close'], quote['Data'][target_idx+i]['Close'])
                test['Data'][0]['Day_' + str(i) + '_Profit'] = day2day_profit
                if(INDEX and INDEX_idx+i < len(INDEX['Data'])):
                    day2day_INDEX_change = self.get_profit_rate(INDEX['Data'][INDEX_idx]['Close'], INDEX['Data'][INDEX_idx+i]['Close'])
                    test['Data'][0]['Day_' + str(i) + '_INDEX_Change'] = day2day_INDEX_change
                    test['Data'][0]['Day_' + str(i) + '_Differ'] = day2day_profit-day2day_INDEX_change
            # print(2222)
            # print(quote['Data'][target_idx])
            # print(custom_buy_point)
            # print(custom_sell_point)
            test['Data'][0]['Custom_Profit'] = self.get_profit_rate(custom_buy_point,custom_sell_point)
            results.append(test)
            
        print("profit_test end... time cost: " + str(round(timeit.default_timer() - start)) + "s" + "\n")
        return results

    def data_load(self, start_date, end_date, output_types):
        all_quotes = self.load_all_quote_symbol()
        print("total " + str(len(all_quotes)) + " quotes are loaded..." + "\n")
        all_quotes = all_quotes
        ## self.load_all_quote_info(all_quotes)
        self.load_all_quote_data(all_quotes, start_date, end_date)
        self.data_process(all_quotes)
        self.data_export(all_quotes, output_types, None)
        self.db_operate(all_quotes,'all_quotes','replace','Symbol')

    def data_statistics(self, data_all):
        statistics = {}
        for data in data_all:
            if data['Data'][0]:
                for day in data['Data'][0].keys():
                    # key = 'Day_'+str(day+1)+'_Profit'
                    key = day
                    profit = data['Data'][0].get(key)
                    if(profit != None):
                        statistics[key] = statistics.get(key,{})
                        statistics[key]['num'] = statistics[key].get('num',0) + 1
                        statistics[key]['profit'] = statistics[key].get('profit',0) + profit
                        statistics[key]['success_num'] = statistics[key].get('success_num',0)
                        if(profit>0):
                            statistics[key]['success_num'] += 1
                            # statistics['success_stock'].append(data['Name'])
        for item in statistics.keys(): 
            statistics[item]['success_rate'] = str(round(statistics[item]['success_num'] / statistics[item]['num'] *100,2))+'%'
            statistics[item]['profit'] = str(round(statistics[item]['profit'] / statistics[item]['num'] * 100 ,3))+'%'
        return statistics

    def data_test(self,all_quotes,target_date, test_range, output_types):
        ## loading test methods
        methods = []
        path = self.testfile_path
        
        ## from mongodb
        if(path == 'mongodb'):
            print("Load testing methods from Mongodb...\n")
            client = MongoClient(self.mongo_url, self.mongo_port)
            db = client[self.database_name]
            col = db[self.collection_name]
            q = None
            if(len(self.methods) > 0):
                applied_methods = list(map(int, self.methods.split(',')))
                q = {"method_id": {"$in": applied_methods}}
            for doc in col.find(q, ['name','desc','method']):
                print(doc)
                m = {'name': doc['name'], 'value_check': self.convert_value_check(doc['method'])}
                methods.append(m)
                
        ## from test file
        else:
            if not os.path.exists(path):
                print("Portfolio test file is not existed, testing is aborted...\n")
                return
            f = io.open(path, 'r', encoding='utf-8')
            for line in f:
                if(line.startswith('##') or len(line.strip()) == 0):
                    continue
                line = line.strip().strip('\n')
                name = line[line.find('[')+1:line.find(']:')]
                value = line[line.find(']:')+2:]
                m = {'name': name, 'value_check': self.convert_value_check(value)}
                methods.append(m)
                
        if(len(methods) == 0):
            print("No method is loaded, testing is aborted...\n")
            return

        ## portfolio testing 
        target_date_time = datetime.datetime.strptime(target_date, "%Y-%m-%d")
        data_all = []
        data_all_dict = []
        for i in range(test_range):
            date = (target_date_time - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
            is_date_valid = self.check_date(all_quotes, date)
            if is_date_valid:
                selected_quotes = self.quote_pick(all_quotes, date, methods)
                res = self.profit_test(selected_quotes, date)
                if(len(selected_quotes)>0):
                    self.data_export(res, output_types, 'result_' + date)
                    data_all.extend(res)
                    data_all_dict.append({'date':date,'result':res})
        if len(data_all_dict):
            self.db_operate(data_all_dict,'results','replace','date')
        data_statistics = self.data_statistics(data_all)
        self.data_export(data_statistics, output_types, 'statistics_all')
        self.db_operate(data_statistics,'data_statistics','replace')

    def run_single_stock(self):
        print('run single stock')
        quote = {"Symbol":self.single_stock,"Name":'test'}
        # lg = bs.login()
        self.load_quote_data(quote,self.start_date, self.end_date,False,[])
        # bs.logout()
        self.data_process([quote])
        self.data_test([quote],self.target_date, self.test_date_range, ['json'])

    def run(self):
        ## test single stock
        if(self.single_stock):
            self.run_single_stock()
            return
        ## output types
        output_types = []
        if(self.output_type == "json"):
            output_types.append("json")
        elif(self.output_type == "csv"):
            output_types.append("csv")
        elif(self.output_type == "all"):
            output_types = ["json", "csv"]
            
        ## loading stock data
        if(self.reload_data == 'Y'):
            print("Start loading stock data...\n")
            self.data_load(self.start_date, self.end_date, output_types)

        ## process stock data 
        if(self.process_data == 'Y' and self.reload_data == 'N'):
            all_quotes = list(self.usedbcol('all_quotes').find())
            self.data_process(all_quotes)
            self.db_operate(all_quotes,'all_quotes','replace','Symbol')

        ## test & generate portfolio
        if(self.gen_portfolio == 'Y'):
            print("Start portfolio testing...\n")
            if "all_quotes" not in dir():
                # all_quotes = self.file_data_load()
                all_quotes = list(self.usedbcol('all_quotes').find())
            self.data_test(all_quotes,self.target_date, self.test_date_range, output_types)
