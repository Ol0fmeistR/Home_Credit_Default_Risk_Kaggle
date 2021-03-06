import pandas as pd
import numpy as np

from sklearn.metrics import roc_auc_score, precision_recall_curve, roc_curve, average_precision_score
from sklearn.model_selection import KFold
from lightgbm import LGBMClassifier

import matplotlib.pyplot as plt
import seaborn as sns
import gc
from functools import partial
from tqdm import tqdm_notebook as tqdm
import multiprocessing as mp



def build_model_input():
    buro_bal = pd.read_csv('../input/bureau_balance.csv')
    print('Buro bal shape : ', buro_bal.shape)
    
    #replacement = {'C':0, '0':0.1, 'X':0, '1':600, '2': 725, '3':800, '4':900, '5':1000}
    #buro_bal['SCORE'] = buro_bal['STATUS'].apply(lambda x: replacement.get(x))
    
    #buro_bal = buro_bal.query('MONTHS_BALANCE != 0')
    #buro_bal['W_SCORES'] = -(buro_bal['SCORE']/buro_bal['MONTHS_BALANCE'])
    
    #del buro_bal['STATUS'] 
    
    print('transform to dummies')
    buro_bal = pd.concat([buro_bal, pd.get_dummies(buro_bal.STATUS, prefix='buro_bal_status')], axis=1).drop('STATUS', axis=1)
    
    print('Counting buros')
    buro_counts = buro_bal[['SK_ID_BUREAU', 'MONTHS_BALANCE']].groupby('SK_ID_BUREAU').count()
    buro_bal['buro_count'] = buro_bal['SK_ID_BUREAU'].map(buro_counts['MONTHS_BALANCE'])
    
    print('averaging buro bal')
    avg_buro_bal = buro_bal.groupby('SK_ID_BUREAU').mean()
    
    avg_buro_bal.columns = ['avg_buro_' + f_ for f_ in avg_buro_bal.columns]
    del buro_bal
    gc.collect()
    
    print('Read Bureau')
    buro = pd.read_csv('../input/bureau.csv')
    
    print('Go to dummies')
    buro_credit_active_dum = pd.get_dummies(buro.CREDIT_ACTIVE, prefix='ca_')
    buro_credit_currency_dum = pd.get_dummies(buro.CREDIT_CURRENCY, prefix='cu_')
    buro_credit_type_dum = pd.get_dummies(buro.CREDIT_TYPE, prefix='ty_')
    
    buro_full = pd.concat([buro, buro_credit_active_dum, buro_credit_currency_dum, buro_credit_type_dum], axis=1)
    # buro_full.columns = ['buro_' + f_ for f_ in buro_full.columns]
    
    # Groupby each Customer and Sort values of DAYS_CREDIT in ascending order
    grp = buro_full[['SK_ID_CURR', 'SK_ID_BUREAU', 'DAYS_CREDIT']].groupby(by = ['SK_ID_CURR'])
    grp1 = grp.apply(lambda x: x.sort_values(['DAYS_CREDIT'], ascending = False)).reset_index(drop = True)
    print("Grouping and Sorting done")
    
    # Calculate Difference between the number of Days 
    grp1['DAYS_CREDIT1'] = grp1['DAYS_CREDIT']*(-1)
    grp1['DAYS_DIFF'] = grp1.groupby(by = ['SK_ID_CURR'])['DAYS_CREDIT1'].diff()
    grp1['DAYS_DIFF'] = grp1['DAYS_DIFF'].fillna(0).astype(int)
    del grp1['DAYS_CREDIT1'], grp1['DAYS_CREDIT'], grp1['SK_ID_CURR']
    gc.collect()
    print("Difference days calculated")
    
    
    buro_full = buro_full.merge(grp1, on = ['SK_ID_BUREAU'], how = 'left')
    del grp1, grp
    gc.collect()
    print("Difference in Dates between Previous CB applications is CALCULATED ")
    print(buro_full.shape)
    
    # % of loans for which credit date was in the past
    buro_full['CREDIT_ENDDATE_BINARY'] = buro_full['DAYS_CREDIT_ENDDATE']

    def f(x):
        if x<0:
            y = 0
        else:
            y = 1   
        return y

    buro_full['CREDIT_ENDDATE_BINARY'] = buro_full.apply(lambda x: f(x.DAYS_CREDIT_ENDDATE), axis = 1)
    print("New Binary Column calculated")

    grp = buro_full.groupby(by = ['SK_ID_CURR'])['CREDIT_ENDDATE_BINARY'].mean().reset_index().rename(index=str, columns={'CREDIT_ENDDATE_BINARY': 'CREDIT_ENDDATE_PERCENTAGE'})
    buro_full = buro_full.merge(grp, on = ['SK_ID_CURR'], how = 'left')

    del buro_full['CREDIT_ENDDATE_BINARY'], grp
    gc.collect()
    print(buro_full.shape)

    #Calculating debt credit ratio
    buro_full['AMT_CREDIT_SUM_DEBT'] = buro_full['AMT_CREDIT_SUM_DEBT'].fillna(0)
    buro_full['AMT_CREDIT_SUM'] = buro_full['AMT_CREDIT_SUM'].fillna(0)

    grp1 = buro_full[['SK_ID_CURR', 'AMT_CREDIT_SUM_DEBT']].groupby(by = ['SK_ID_CURR'])['AMT_CREDIT_SUM_DEBT'].sum().reset_index().rename( index = str, columns = { 'AMT_CREDIT_SUM_DEBT': 'TOTAL_CUSTOMER_DEBT'})
    grp2 = buro_full[['SK_ID_CURR', 'AMT_CREDIT_SUM']].groupby(by = ['SK_ID_CURR'])['AMT_CREDIT_SUM'].sum().reset_index().rename( index = str, columns = { 'AMT_CREDIT_SUM': 'TOTAL_CUSTOMER_CREDIT'})

    buro_full = buro_full.merge(grp1, on = ['SK_ID_CURR'], how = 'left')
    buro_full = buro_full.merge(grp2, on = ['SK_ID_CURR'], how = 'left')
    del grp1, grp2
    gc.collect()

    buro_full['DEBT_CREDIT_RATIO'] = buro_full['TOTAL_CUSTOMER_DEBT']/buro_full['TOTAL_CUSTOMER_CREDIT']

    del buro_full['TOTAL_CUSTOMER_DEBT'], buro_full['TOTAL_CUSTOMER_CREDIT']
    gc.collect()
    print(buro_full.shape)
    
    #Average number of loans prolonged
    buro_full['CNT_CREDIT_PROLONG'] = buro_full['CNT_CREDIT_PROLONG'].fillna(0)
    grp = buro_full[['SK_ID_CURR', 'CNT_CREDIT_PROLONG']].groupby(by = ['SK_ID_CURR'])['CNT_CREDIT_PROLONG'].mean().reset_index().rename( index = str, columns = { 'CNT_CREDIT_PROLONG': 'AVG_CREDITDAYS_PROLONGED'})
    buro_full = buro_full.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    print(buro_full.shape)
    
    del grp
    
    # Fraction of total debt that is overdue per customer(added in latest version)
    """buro_full['AMT_CREDIT_SUM_DEBT'] = buro_full['AMT_CREDIT_SUM_DEBT'].fillna(0)
    buro_full['AMT_CREDIT_SUM_OVERDUE'] = buro_full['AMT_CREDIT_SUM_OVERDUE'].fillna(0)

    grp1 = buro_full[['SK_ID_CURR', 'AMT_CREDIT_SUM_DEBT']].groupby(by = ['SK_ID_CURR'])['AMT_CREDIT_SUM_DEBT'].sum().reset_index().rename( index = str, columns = { 'AMT_CREDIT_SUM_DEBT': 'TOTAL_CUSTOMER_DEBT'})
    grp2 = buro_full[['SK_ID_CURR', 'AMT_CREDIT_SUM_OVERDUE']].groupby(by = ['SK_ID_CURR'])['AMT_CREDIT_SUM_OVERDUE'].sum().reset_index().rename( index = str, columns = { 'AMT_CREDIT_SUM_OVERDUE': 'TOTAL_CUSTOMER_OVERDUE'})

    buro_full = buro_full.merge(grp1, on = ['SK_ID_CURR'], how = 'left')
    buro_full = buro_full.merge(grp2, on = ['SK_ID_CURR'], how = 'left')
    del grp1, grp2
    gc.collect()

    buro_full['OVERDUE_DEBT_RATIO'] = buro_full['TOTAL_CUSTOMER_OVERDUE']/buro_full['TOTAL_CUSTOMER_DEBT']

    del buro_full['TOTAL_CUSTOMER_OVERDUE'], buro_full['TOTAL_CUSTOMER_DEBT']
    gc.collect()"""
    
    # % of active loans from bureau data(added in latest version)
    # Create a new dummy column for whether CREDIT is ACTIVE OR CLOSED 
    """buro_full['CREDIT_ACTIVE_BINARY'] = buro_full['CREDIT_ACTIVE']

    def f(x):
        if x == 'Closed':
            y = 0
        else:
            y = 1    
        return y

    buro_full['CREDIT_ACTIVE_BINARY'] = buro_full.apply(lambda x: f(x.CREDIT_ACTIVE), axis = 1)

    # Calculate mean number of loans that are ACTIVE per CUSTOMER 
    grp = buro_full.groupby(by = ['SK_ID_CURR'])['CREDIT_ACTIVE_BINARY'].mean().reset_index().rename(index=str, columns={'CREDIT_ACTIVE_BINARY': 'ACTIVE_LOANS_PERCENTAGE'})
    buro_full = buro_full.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del buro_full['CREDIT_ACTIVE_BINARY'], grp
    gc.collect()
    print(buro_full.shape)"""
    
    #Loan diversity of the applicant(added on latest version)
    # Number of Loans per Customer
    """grp9 = buro_full[['SK_ID_CURR', 'DAYS_CREDIT']].groupby(by = ['SK_ID_CURR'])['DAYS_CREDIT'].count().reset_index().rename(index=str, columns={'DAYS_CREDIT': 'BUREAU_LOAN_COUNT'})
    buro_full = buro_full.merge(grp9, on = ['SK_ID_CURR'], how = 'left')

    # Number of types of Credit loans for each Customer 
    grp10 = buro_full[['SK_ID_CURR', 'CREDIT_TYPE']].groupby(by = ['SK_ID_CURR'])['CREDIT_TYPE'].nunique().reset_index().rename(index=str, columns={'CREDIT_TYPE': 'BUREAU_LOAN_TYPES'})
    buro_full = buro_full.merge(grp10, on = ['SK_ID_CURR'], how = 'left')

    # Average Number of Loans per Loan Type
    buro_full['AVERAGE_LOAN_TYPE'] = buro_full['BUREAU_LOAN_COUNT']/buro_full['BUREAU_LOAN_TYPES']
    del buro_full['BUREAU_LOAN_COUNT'], buro_full['BUREAU_LOAN_TYPES'], grp9, grp10
    gc.collect()"""
    
    
    del buro_credit_active_dum, buro_credit_currency_dum, buro_credit_type_dum
    gc.collect()
    
    #NEW FEATURE ADDED
    
    #END OF NEW FEATURE
    
    
    print('Merge with buro avg')
    buro_full = buro_full.merge(right=avg_buro_bal.reset_index(), how='left', on='SK_ID_BUREAU', suffixes=('', '_bur_bal'))
    
    print('Counting buro per SK_ID_CURR')
    nb_bureau_per_curr = buro_full[['SK_ID_CURR', 'SK_ID_BUREAU']].groupby('SK_ID_CURR').count()
    buro_full['SK_ID_BUREAU'] = buro_full['SK_ID_CURR'].map(nb_bureau_per_curr['SK_ID_BUREAU'])
    
    print('Averaging bureau')
    avg_buro = buro_full.groupby('SK_ID_CURR').mean()
    print(avg_buro.head())
    
    del buro, buro_full
    gc.collect()
    
    print('Read prev')
    pv = pd.read_csv('../input/previous_application.csv')
    
    # Days 365243 values -> nan
    pv['DAYS_FIRST_DRAWING'].replace(365243, np.nan, inplace= True)
    pv['DAYS_FIRST_DUE'].replace(365243, np.nan, inplace= True)
    pv['DAYS_LAST_DUE_1ST_VERSION'].replace(365243, np.nan, inplace= True)
    pv['DAYS_LAST_DUE'].replace(365243, np.nan, inplace= True)
    pv['DAYS_TERMINATION'].replace(365243, np.nan, inplace= True)
    
    #categorical features begin
    
    #categorical features end
    
    #do all feature engg for previous_application here
    #Difference of amount application and amount credit(feature engg for prev application)
    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_CREDIT'].sum().reset_index().rename(index = str, columns = {'AMT_CREDIT' : 'AMT_CREDIT_RECEIVED'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_APPLICATION'].sum().reset_index().rename(index = str, columns = {'AMT_APPLICATION' : 'AMT_APPLIED_FOR'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    pv['DIFF'] = (pv['AMT_CREDIT_RECEIVED'] - pv['AMT_APPLIED_FOR'])
    del pv['AMT_CREDIT_RECEIVED']
    del pv['AMT_APPLIED_FOR']
    gc.collect()

    grp = pv.groupby(by = ['SK_ID_CURR'])['DIFF'].mean().reset_index().rename(index = str, columns ={ 'DIFF' : 'DIFF_CREDIT_APPLIED'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    del pv['DIFF']
    gc.collect()
    
    #Difference of amount credit and amount annutiy(feature engg for prev application)
    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_CREDIT'].sum().reset_index().rename(index = str, columns = {'AMT_CREDIT' : 'AMT_CREDIT_RECEIVED'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_ANNUITY'].sum().reset_index().rename(index = str, columns = {'AMT_ANNUITY' : 'AMT_ANNUITY_RECEIVED'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    pv['DIFF'] = (pv['AMT_CREDIT_RECEIVED'] - pv['AMT_ANNUITY_RECEIVED'])
    del pv['AMT_CREDIT_RECEIVED']
    del pv['AMT_ANNUITY_RECEIVED']
    gc.collect()

    grp = pv.groupby(by = ['SK_ID_CURR'])['DIFF'].mean().reset_index().rename(index = str, columns ={ 'DIFF' : 'DIFF_CREDIT_ANNUITY'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    del pv['DIFF']
    gc.collect() 
    
    #Number of types of past loans in previous application per customer
    grp = pv[['SK_ID_CURR', 'NAME_CONTRACT_TYPE']].groupby(by = ['SK_ID_CURR'])['NAME_CONTRACT_TYPE'].nunique().reset_index().rename(index=str, columns={'NAME_CONTRACT_TYPE': 'LOAN_TYPE'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    print(pv.shape)
    
    #Profit remaining after subtracting goods price from credit and annuity
    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_CREDIT'].sum().reset_index().rename(index = str, columns = {'AMT_CREDIT' : 'AMT_CREDIT_RECEIVED'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_ANNUITY'].sum().reset_index().rename(index = str, columns = {'AMT_ANNUITY' : 'AMT_ANNUITY_RECEIVED'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_GOODS_PRICE'].sum().reset_index().rename(index = str, columns = {'AMT_GOODS_PRICE' : 'PRICE_OF_GOODS'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    pv['PROF'] = ((pv['AMT_CREDIT_RECEIVED'] + pv['AMT_ANNUITY_RECEIVED'])-(pv['PRICE_OF_GOODS']))/(pv['AMT_CREDIT_RECEIVED'] + pv['AMT_ANNUITY_RECEIVED'])
    del pv['AMT_CREDIT_RECEIVED']
    del pv['AMT_ANNUITY_RECEIVED']
    del pv['PRICE_OF_GOODS']
    gc.collect()

    grp = pv.groupby(by = ['SK_ID_CURR'])['PROF'].mean().reset_index().rename(index = str, columns ={ 'PROF' : 'DEBT_INCOME_RATIO'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    del pv['PROF']
    gc.collect()
    
    #New features added for previous application(latest version)
    #How many previous applications of our client was rejected, approved, denied etc
    grp = pv[['SK_ID_CURR', 'NAME_CONTRACT_STATUS']].groupby(by = ['SK_ID_CURR'])['NAME_CONTRACT_STATUS'].nunique().reset_index().rename(index=str, columns={'NAME_CONTRACT_STATUS': 'STATUS?'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    print(pv.shape)
    del grp
    
    #Lets check how many loans were taken for cash, POS, car etc
    grp = pv[['SK_ID_CURR', 'NAME_PORTFOLIO']].groupby(by = ['SK_ID_CURR'])['NAME_PORTFOLIO'].nunique().reset_index().rename(index=str, columns={'NAME_PORTFOLIO': 'LOAN_FOR?'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    print(pv.shape)
    del grp
    
    pv['NFLAG_INSURED_ON_APPROVAL'] = pv['NFLAG_INSURED_ON_APPROVAL'].fillna(0)
    #Now lets check for how many of the previous applications was the applicant insured
    grp = pv[['SK_ID_CURR', 'NFLAG_INSURED_ON_APPROVAL']].groupby(by = ['SK_ID_CURR'])['NFLAG_INSURED_ON_APPROVAL'].nunique().reset_index().rename(index=str, columns={'NFLAG_INSURED_ON_APPROVAL': 'INSURED?'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    print(pv.shape)
    del grp
    
    #Let's check the interest grouping in previous applications per client
    grp = pv[['SK_ID_CURR', 'NAME_YIELD_GROUP']].groupby(by = ['SK_ID_CURR'])['NAME_YIELD_GROUP'].nunique().reset_index().rename(index=str, columns={'NAME_YIELD_GROUP': 'INT_GROUP'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    print(pv.shape)
    del grp
    
    #counting the various diff types of pdt combinations for every applicant's previous loan
    grp = pv[['SK_ID_CURR', 'PRODUCT_COMBINATION']].groupby(by = ['SK_ID_CURR'])['PRODUCT_COMBINATION'].nunique().reset_index().rename(index=str, columns={'PRODUCT_COMBINATION': 'COMBINATION_COUNT'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    print(pv.shape)
    del grp
    
    #counting the no. of various different channels through which the applicants acquired the previous loan
    grp = pv[['SK_ID_CURR', 'CHANNEL_TYPE']].groupby(by = ['SK_ID_CURR'])['CHANNEL_TYPE'].nunique().reset_index().rename(index=str, columns={'CHANNEL_TYPE': 'CHANNEL?'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    print(pv.shape)
    del grp
    
    #Ratio of amount credit and amount annutiy(feature engg for prev application) #new
    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_CREDIT'].sum().reset_index().rename(index = str, columns = {'AMT_CREDIT' : 'AMT_CREDIT_RECEIVED'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_ANNUITY'].sum().reset_index().rename(index = str, columns = {'AMT_ANNUITY' : 'AMT_ANNUITY_RECEIVED'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    pv['RATIO'] = (pv['AMT_ANNUITY_RECEIVED'] / pv['AMT_CREDIT_RECEIVED'])
    del pv['AMT_CREDIT_RECEIVED']
    del pv['AMT_ANNUITY_RECEIVED']
    gc.collect()

    grp = pv.groupby(by = ['SK_ID_CURR'])['RATIO'].mean().reset_index().rename(index = str, columns ={ 'RATIO' : 'RATIO_ANNUITY_CREDIT'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    del pv['RATIO']
    gc.collect() 
    
    #Ratio of amount application and amount credit(feature engg for prev application) #new
    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_CREDIT'].sum().reset_index().rename(index = str, columns = {'AMT_CREDIT' : 'AMT_CREDIT_RECEIVED'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_APPLICATION'].sum().reset_index().rename(index = str, columns = {'AMT_APPLICATION' : 'AMT_APPLIED_FOR'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    pv['RATIO'] = (pv['AMT_APPLIED_FOR'] / pv['AMT_CREDIT_RECEIVED'])
    del pv['AMT_CREDIT_RECEIVED']
    del pv['AMT_APPLIED_FOR']
    gc.collect()

    grp = pv.groupby(by = ['SK_ID_CURR'])['RATIO'].mean().reset_index().rename(index = str, columns ={ 'RATIO' : 'RATIO_CREDIT_APPLIED'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    del pv['RATIO']
    gc.collect()
    
    #START OF MIN MAX FEATURES
    
    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_APPLICATION'].max().reset_index().rename(index = str, columns ={ 'AMT_APPLICATION' : 'MAX_AMT_APPL'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_APPLICATION'].min().reset_index().rename(index = str, columns ={ 'AMT_APPLICATION' : 'MIN_AMT_APPL'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_ANNUITY'].max().reset_index().rename(index = str, columns ={ 'AMT_ANNUITY' : 'MAX_AMT_ANN'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_ANNUITY'].min().reset_index().rename(index = str, columns ={ 'AMT_ANNUITY' : 'MIN_AMT_ANN'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_CREDIT'].max().reset_index().rename(index = str, columns ={ 'AMT_CREDIT' : 'MAX_AMT_CRE'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_CREDIT'].min().reset_index().rename(index = str, columns ={ 'AMT_CREDIT' : 'MIN_AMT_CRE'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_GOODS_PRICE'].max().reset_index().rename(index = str, columns ={ 'AMT_GOODS_PRICE' : 'MAX_AMT_PRICE'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = pv.groupby(by = ['SK_ID_CURR'])['AMT_GOODS_PRICE'].min().reset_index().rename(index = str, columns ={ 'AMT_GOODS_PRICE' : 'MIN_AMT_PRICE'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = pv.groupby(by = ['SK_ID_CURR'])['DAYS_DECISION'].min().reset_index().rename(index = str, columns = {'DAYS_DECISION' : 'MIN_DAYS'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = pv.groupby(by = ['SK_ID_CURR'])['DAYS_DECISION'].max().reset_index().rename(index = str, columns = {'DAYS_DECISION' : 'MAX_DAYS'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = pv.groupby(by = ['SK_ID_CURR'])['DAYS_FIRST_DUE'].min().reset_index().rename(index = str, columns = {'DAYS_FIRST_DUE' : 'MIN_DAYS_DUE'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = pv.groupby(by = ['SK_ID_CURR'])['DAYS_FIRST_DUE'].max().reset_index().rename(index = str, columns = {'DAYS_FIRST_DUE' : 'MAX_DAYS_DUE'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = pv.groupby(by = ['SK_ID_CURR'])['DAYS_LAST_DUE_1ST_VERSION'].min().reset_index().rename(index = str, columns = {'DAYS_LAST_DUE_1ST_VERSION' : 'MIN_DAYS_LAST'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = pv.groupby(by = ['SK_ID_CURR'])['DAYS_LAST_DUE_1ST_VERSION'].max().reset_index().rename(index = str, columns = {'DAYS_LAST_DUE_1ST_VERSION' : 'MAX_DAYS_LAST'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = pv.groupby(by = ['SK_ID_CURR'])['DAYS_LAST_DUE'].max().reset_index().rename(index = str, columns = {'DAYS_LAST_DUE' : 'MAX_DAYS_LAST_DUE'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = pv.groupby(by = ['SK_ID_CURR'])['DAYS_LAST_DUE'].min().reset_index().rename(index = str, columns = {'DAYS_LAST_DUE' : 'MIN_DAYS_LAST_DUE'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = pv.groupby(by = ['SK_ID_CURR'])['DAYS_TERMINATION'].min().reset_index().rename(index = str, columns = {'DAYS_TERMINATION' : 'MIN_TERMINATION'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = pv.groupby(by = ['SK_ID_CURR'])['DAYS_TERMINATION'].max().reset_index().rename(index = str, columns = {'DAYS_TERMINATION' : 'MAX_TERMINATION'})
    pv = pv.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    print("Done with feature engg for previous applications")
    
    prev_cat_features = [
        f_ for f_ in pv.columns if pv[f_].dtype == 'object'
    ]
    
    print('Go to dummies')
    prev_dum = pd.DataFrame()
    for f_ in prev_cat_features:
        prev_dum = pd.concat([prev_dum, pd.get_dummies(pv[f_], prefix=f_).astype(np.uint8)], axis=1)
    
    pv = pd.concat([pv, prev_dum], axis=1)
    
    
    del prev_dum
    gc.collect()
    
    print('Counting number of Prevs')
    nb_prev_per_curr = pv[['SK_ID_CURR', 'SK_ID_PREV']].groupby('SK_ID_CURR').count()
    pv['SK_ID_PREV'] = pv['SK_ID_CURR'].map(nb_prev_per_curr['SK_ID_PREV'])
    
    
    print('Averaging prev')
    avg_prev = pv.groupby('SK_ID_CURR').mean()
    print(avg_prev.head())
    del pv
    gc.collect()
    
    print('Reading POS_CASH')
    ps = pd.read_csv('../input/POS_CASH_balance.csv')
    
    print('Go to dummies')
    ps = pd.concat([ps, pd.get_dummies(ps['NAME_CONTRACT_STATUS'])], axis=1)
    
    """KAMIL FEATURES"""
    """features = pd.DataFrame({'SK_ID_CURR': ps['SK_ID_CURR'].unique()})
    
    pos_cash_sorted = ps.sort_values(['SK_ID_CURR', 'MONTHS_BALANCE'])
    group_object = pos_cash_sorted.groupby('SK_ID_CURR')['CNT_INSTALMENT_FUTURE'].last().reset_index()
    group_object.rename(index=str,
                        columns={'CNT_INSTALMENT_FUTURE': 'pos_cash_remaining_installments'},
                        inplace=True)
    
    features = features.merge(group_object, on=['SK_ID_CURR'], how='left')
    features.head()
    
    ps['is_contract_status_completed'] = ps['NAME_CONTRACT_STATUS'] == 'Completed'
    group_object = ps.groupby(['SK_ID_CURR'])['is_contract_status_completed'].sum().reset_index()
    group_object.rename(index=str,
                        columns={'is_contract_status_completed': 'pos_cash_completed_contracts'},
                        inplace=True)
    features = features.merge(group_object, on=['SK_ID_CURR'], how='left')
    
    ps['pos_cash_paid_late'] = (ps['SK_DPD'] > 0).astype(int)
    ps['pos_cash_paid_late_with_tolerance'] = (ps['SK_DPD_DEF'] > 0).astype(int)
    groupby = ps.groupby(['SK_ID_CURR'])
    
    ps = ps.merge(features, on='SK_ID_CURR', how='left')
    
    
    def  last_k_installment_features(gr, periods):
        gr_ = gr.copy()
        gr_.sort_values(['MONTHS_BALANCE'], ascending=False, inplace=True)
    
        features = {}
        for period in periods:
            if period > 10e10:
                period_name = 'all_installment_'
                gr_period = gr_.copy()
            else:
                period_name = 'last_{}_'.format(period)
                gr_period = gr_.iloc[:period]
    
            features = add_features_in_group(features, gr_period, 'pos_cash_paid_late',
                                                 ['count', 'mean'],
                                                 period_name)
            features = add_features_in_group(features, gr_period, 'pos_cash_paid_late_with_tolerance',
                                                 ['count', 'mean'],
                                                 period_name)
            features = add_features_in_group(features, gr_period, 'SK_DPD',
                                                 ['sum', 'mean', 'max', 'min', 'median'],
                                                 period_name)
            features = add_features_in_group(features, gr_period, 'SK_DPD_DEF',
                                                 ['sum', 'mean', 'max', 'min','median'],
                                                 period_name)
        return features
        
    
    def parallel_apply(groups, func, index_name='Index', num_workers=1, chunk_size=100000):
        n_chunks = np.ceil(1.0 * groups.ngroups / chunk_size)
        indeces, features = [], []
        for index_chunk, groups_chunk in tqdm(chunk_groups(groups, chunk_size), total=n_chunks):
            with mp.pool.Pool(num_workers) as executor:
                features_chunk = executor.map(func, groups_chunk)
            features.extend(features_chunk)
            indeces.extend(index_chunk)
    
        features = pd.DataFrame(features)
        features.index = indeces
        features.index.name = index_name
        return features
        
    def chunk_groups(groupby_object, chunk_size):
        n_groups = groupby_object.ngroups
        group_chunk, index_chunk = [], []
        for i, (index, df) in enumerate(groupby_object):
            group_chunk.append(df)
            index_chunk.append(index)
    
            if (i + 1) % chunk_size == 0 or i + 1 == n_groups:
                group_chunk_, index_chunk_ = group_chunk.copy(), index_chunk.copy()
                group_chunk, index_chunk = [], []
                yield index_chunk_, group_chunk_
    
    from tqdm import tqdm_notebook as tqdm
    import multiprocessing as mp
    
    def add_features_in_group(features, gr_, feature_name, aggs, prefix):
        for agg in aggs:
            if agg == 'sum':
                features['{}{}_sum'.format(prefix, feature_name)] = gr_[feature_name].sum()
            elif agg == 'mean':
                features['{}{}_mean'.format(prefix, feature_name)] = gr_[feature_name].mean()
            elif agg == 'max':
                features['{}{}_max'.format(prefix, feature_name)] = gr_[feature_name].max()
            elif agg == 'min':
                features['{}{}_min'.format(prefix, feature_name)] = gr_[feature_name].min()
            elif agg == 'std':
                features['{}{}_std'.format(prefix, feature_name)] = gr_[feature_name].std()
            elif agg == 'count':
                features['{}{}_count'.format(prefix, feature_name)] = gr_[feature_name].count()
            elif agg == 'skew':
                features['{}{}_skew'.format(prefix, feature_name)] = skew(gr_[feature_name])
            elif agg == 'kurt':
                features['{}{}_kurt'.format(prefix, feature_name)] = kurtosis(gr_[feature_name])
            elif agg == 'iqr':
                features['{}{}_iqr'.format(prefix, feature_name)] = iqr(gr_[feature_name])
            elif agg == 'median':
                features['{}{}_median'.format(prefix, feature_name)] = gr_[feature_name].median()
    
        return features

    features = pd.DataFrame({'SK_ID_CURR': ps['SK_ID_CURR'].unique()})
    func = partial(last_k_installment_features, periods=[1, 10, 50, 10e16])
    g = parallel_apply(groupby, func, index_name='SK_ID_CURR', num_workers=10, chunk_size=10000).reset_index()
    features = features.merge(g, on='SK_ID_CURR', how='left')
    
    ps = ps.merge(features, on='SK_ID_CURR', how='left')
    
    print("Adding last loan feature")
    #LAST LOAN FEATURE
    def last_loan_features(gr):
        gr_ = gr.copy()
        gr_.sort_values(['MONTHS_BALANCE'], ascending=False, inplace=True)
        last_installment_id = gr_['SK_ID_PREV'].iloc[0]
        gr_ = gr_[gr_['SK_ID_PREV'] == last_installment_id]
    
        features={}
        features = add_features_in_group(features, gr_, 'pos_cash_paid_late',
                                             ['count', 'sum', 'mean'],
                                             'last_loan_')
        features = add_features_in_group(features, gr_, 'pos_cash_paid_late_with_tolerance',
                                             ['sum', 'mean'],
                                             'last_loan_')
        features = add_features_in_group(features, gr_, 'SK_DPD',
                                             ['sum', 'mean', 'max', 'min', 'std'],
                                             'last_loan_')
        features = add_features_in_group(features, gr_, 'SK_DPD_DEF',
                                             ['sum', 'mean', 'max', 'min', 'std'],
                                             'last_loan_')
        return features
    
    features = pd.DataFrame({'SK_ID_CURR': ps['SK_ID_CURR'].unique()})
    g = parallel_apply(groupby, last_loan_features, index_name='SK_ID_CURR', num_workers=10, chunk_size=10000).reset_index()
    features = features.merge(g, on='SK_ID_CURR', how='left')
    features.head()
    
    ps = ps.merge(features, on='SK_ID_CURR', how='left')
    print("Done with Kamil's features")"""

    #Feature engg for pos_cash_balance
    #Ratio of unpaid and paid installments
    grp = ps.groupby(by = ['SK_ID_CURR'])['CNT_INSTALMENT'].sum().reset_index().rename(index = str, columns = {'CNT_INSTALMENT' : 'CNT_INSTALMENT_PAID'})
    ps = ps.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    grp = ps.groupby(by = ['SK_ID_CURR'])['CNT_INSTALMENT_FUTURE'].sum().reset_index().rename(index = str, columns = {'CNT_INSTALMENT_FUTURE' : 'CNT_INSTALMENT_TOPAY'})
    ps = ps.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    ps['RATIO'] = (ps['CNT_INSTALMENT_PAID'] / ps['CNT_INSTALMENT_TOPAY'])
    del ps['CNT_INSTALMENT_PAID']
    del ps['CNT_INSTALMENT_TOPAY']
    gc.collect()

    grp = ps.groupby(by = ['SK_ID_CURR'])['RATIO'].mean().reset_index().rename(index = str, columns ={ 'RATIO' : 'RATIO_PAY_TOPAY'})
    ps = ps.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    del ps['RATIO']
    gc.collect
    
    #Avg number of times DPD has occured
    def f(DPD):
    
        # DPD is a series of values of SK_DPD for each of the groupby combination 
        # We convert it to a list to get the number of SK_DPD values NOT EQUALS ZERO
        x = DPD.tolist()
        c = 0
        for i,j in enumerate(x):
            if j != 0:
                c += 1
    
        return c 
        
    grp = ps.groupby(by = ['SK_ID_CURR', 'SK_ID_PREV']).apply(lambda x: f(x.SK_DPD)).reset_index().rename(index = str, columns = {0: 'NO_DPD'})
    grp1 = grp.groupby(by = ['SK_ID_CURR'])['NO_DPD'].mean().reset_index().rename(index = str, columns = {'NO_DPD' : 'DPD_COUNT'})

    ps = ps.merge(grp1, on = ['SK_ID_CURR'], how = 'left')
    del grp1
    del grp 
    gc.collect()
    
    #Average of days past dues per customer
    grp = ps.groupby(by= ['SK_ID_CURR'])['SK_DPD'].mean().reset_index().rename(index = str, columns = {'SK_DPD': 'AVG_DPD'})
    ps = ps.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    #Average of days past dues with tolerance
    grp = ps.groupby(by= ['SK_ID_CURR'])['SK_DPD_DEF'].mean().reset_index().rename(index = str, columns = {'SK_DPD_DEF': 'AVG_DPD_DEF'})
    ps = ps.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    #START OF MIN MAX FEATURES:
    grp = ps.groupby(by = ['SK_ID_CURR'])['MONTHS_BALANCE'].min().reset_index().rename(index = str, columns ={ 'MONTHS_BALANCE' : 'MIN_BAL'})
    ps = ps.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = ps.groupby(by = ['SK_ID_CURR'])['MONTHS_BALANCE'].max().reset_index().rename(index = str, columns ={ 'MONTHS_BALANCE' : 'MAX_BAL'})
    ps = ps.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = ps.groupby(by = ['SK_ID_CURR'])['CNT_INSTALMENT'].min().reset_index().rename(index = str, columns ={ 'CNT_INSTALMENT' : 'MIN_CNT'})
    ps = ps.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = ps.groupby(by = ['SK_ID_CURR'])['CNT_INSTALMENT'].max().reset_index().rename(index = str, columns ={ 'CNT_INSTALMENT' : 'MAX_CNT'})
    ps = ps.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = ps.groupby(by = ['SK_ID_CURR'])['CNT_INSTALMENT_FUTURE'].min().reset_index().rename(index = str, columns ={ 'CNT_INSTALMENT_FUTURE' : 'MIN_CNT_FUT'})
    ps = ps.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = ps.groupby(by = ['SK_ID_CURR'])['CNT_INSTALMENT_FUTURE'].max().reset_index().rename(index = str, columns ={ 'CNT_INSTALMENT_FUTURE' : 'MAX_CNT_FUT'})
    ps = ps.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    
    
    print('Compute nb of prevs per curr')
    nb_prevs = ps[['SK_ID_CURR', 'SK_ID_PREV']].groupby('SK_ID_CURR').count()
    ps['SK_ID_PREV'] = ps['SK_ID_CURR'].map(nb_prevs['SK_ID_PREV'])
    
    print('Go to weighted averages')
    wm = lambda x: np.average(x, weights=-1/ps.loc[x.index, 'MONTHS_BALANCE'])
    f = {'CNT_INSTALMENT': wm, 'CNT_INSTALMENT_FUTURE': wm, 'SK_DPD': wm, 'SK_DPD_DEF':wm, 'MIN_CNT_FUT':wm, 'MAX_CNT_FUT':wm,
            'MIN_CNT':wm, 'MAX_CNT':wm}
    #avg_pos = ps.groupby('SK_ID_CURR').mean()
    avg_pos = ps.groupby('SK_ID_CURR')['CNT_INSTALMENT','CNT_INSTALMENT_FUTURE',
                                                'SK_DPD', 'SK_DPD_DEF', 'MAX_CNT', 'MIN_CNT', 'MAX_CNT_FUT', 'MIN_CNT_FUT'].agg(f)

    del ps, nb_prevs
    gc.collect()
    print("Done with POS_CASH_BALANCE")
    
    print('Reading CC balance')
    cc_bal = pd.read_csv('../input/credit_card_balance.csv')
    
     # Replace some outliers
    #cc_bal.loc[cc_bal['AMT_PAYMENT_CURRENT'] > 4000000, 'AMT_PAYMENT_CURRENT'] = np.nan
    #cc_bal.loc[cc_bal['AMT_CREDIT_LIMIT_ACTUAL'] > 1000000, 'AMT_CREDIT_LIMIT_ACTUAL'] = np.nan
    
    print('Go to dummies')
    cc_bal = pd.concat([cc_bal, pd.get_dummies(cc_bal['NAME_CONTRACT_STATUS'], prefix='cc_bal_status_')], axis=1)
    
    #Calculating number of loans per customer
    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['SK_ID_PREV'].nunique().reset_index().rename(index = str, columns = {'SK_ID_PREV': 'NO_LOANS'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    print(cc_bal.dtypes)
    
    #No of installments paid per loan per customer
    grp = cc_bal.groupby(by = ['SK_ID_CURR', 'SK_ID_PREV'])['CNT_INSTALMENT_MATURE_CUM'].max().reset_index().rename(index = str, columns = {'CNT_INSTALMENT_MATURE_CUM': 'NO_INSTALMENTS'})
    grp1 = grp.groupby(by = ['SK_ID_CURR'])['NO_INSTALMENTS'].sum().reset_index().rename(index = str, columns = {'NO_INSTALMENTS': 'TOTAL_INSTALMENTS'})
    cc_bal = cc_bal.merge(grp1, on = ['SK_ID_CURR'], how = 'left')
    del grp, grp1
    gc.collect()
    
    #Avg no of installments paid per loan
    cc_bal['INSTALLMENTS_PER_LOAN'] = (cc_bal['TOTAL_INSTALMENTS']/cc_bal['NO_LOANS']).astype(int)
    del cc_bal['TOTAL_INSTALMENTS']
    del cc_bal['NO_LOANS']
    gc.collect()
    
    #Avg number of times DPD has occured
    def f(DPD):
    
        # DPD is a series of values of SK_DPD for each of the groupby combination 
        # We convert it to a list to get the number of SK_DPD values NOT EQUALS ZERO
        x = DPD.tolist()
        c = 0
        for i,j in enumerate(x):
            if j != 0:
                c += 1
    
        return c 
        
    grp = cc_bal.groupby(by = ['SK_ID_CURR', 'SK_ID_PREV']).apply(lambda x: f(x.SK_DPD)).reset_index().rename(index = str, columns = {0: 'NO_DPD'})
    grp1 = grp.groupby(by = ['SK_ID_CURR'])['NO_DPD'].mean().reset_index().rename(index = str, columns = {'NO_DPD' : 'DPD_COUNT'})

    cc_bal = cc_bal.merge(grp1, on = ['SK_ID_CURR'], how = 'left')
    del grp1
    del grp 
    gc.collect()
    
    #Average of days past dues per customer
    grp = cc_bal.groupby(by= ['SK_ID_CURR'])['SK_DPD'].mean().reset_index().rename(index = str, columns = {'SK_DPD': 'AVG_DPD'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    # % of minimum payments missed
    def f(min_pay, total_pay):
    
        M = min_pay.tolist()
        T = total_pay.tolist()
        P = len(M)
        c = 0 
        # Find the count of transactions when Payment made is less than Minimum Payment 
        for i in range(len(M)):
            if T[i] < M[i]:
                c += 1  
        return (100*c)/P

    grp = cc_bal.groupby(by = ['SK_ID_CURR']).apply(lambda x: f(x.AMT_INST_MIN_REGULARITY, x.AMT_PAYMENT_CURRENT)).reset_index().rename(index = str, columns = { 0 : 'PERCENTAGE_MISSED_PAYMENTS'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    #Ratio of cash vs card swipes : checking if a customer used credit card more frequently than cash
    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_DRAWINGS_ATM_CURRENT'].sum().reset_index().rename(index = str, columns = {'AMT_DRAWINGS_ATM_CURRENT' : 'DRAWINGS_ATM'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_DRAWINGS_CURRENT'].sum().reset_index().rename(index = str, columns = {'AMT_DRAWINGS_CURRENT' : 'DRAWINGS_TOTAL'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    cc_bal['CASH_CARD_RATIO1'] = (cc_bal['DRAWINGS_ATM']/cc_bal['DRAWINGS_TOTAL'])*100
    del cc_bal['DRAWINGS_ATM']
    del cc_bal['DRAWINGS_TOTAL']
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['CASH_CARD_RATIO1'].mean().reset_index().rename(index = str, columns ={ 'CASH_CARD_RATIO1' : 'CASH_CARD_RATIO'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    del cc_bal['CASH_CARD_RATIO1']
    gc.collect()

    
    #We are going to check the balance and subtract the total drawing from the balance for previous credit
    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_DRAWINGS_ATM_CURRENT'].sum().reset_index().rename(index = str, columns = {'AMT_DRAWINGS_ATM_CURRENT' : 'DRAWINGS_ATM'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_DRAWINGS_CURRENT'].sum().reset_index().rename(index = str, columns = {'AMT_DRAWINGS_CURRENT' : 'DRAWINGS_CURRENT'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_DRAWINGS_OTHER_CURRENT'].sum().reset_index().rename(index = str, columns = {'AMT_DRAWINGS_OTHER_CURRENT' : 'DRAWINGS_OTHER'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_DRAWINGS_POS_CURRENT'].sum().reset_index().rename(index = str, columns = {'AMT_DRAWINGS_POS_CURRENT' : 'DRAWINGS_POS'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    cc_bal['total_drawings'] = cc_bal['DRAWINGS_ATM'] + cc_bal['DRAWINGS_CURRENT'] + cc_bal['DRAWINGS_OTHER'] + cc_bal['DRAWINGS_POS']

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_BALANCE'].sum().reset_index().rename(index = str, columns = {'AMT_BALANCE' : 'BALANCE_LEFT'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    cc_bal['BALANCE_DRAWINGS_RATIO'] = cc_bal['BALANCE_LEFT'] / cc_bal['total_drawings']
    cc_bal['BALANCE_DRAWINGS_DIFF'] = cc_bal['BALANCE_LEFT'] - cc_bal['total_drawings']
    cc_bal['BALANCE_DRAWINGS_DIFF'] = cc_bal['BALANCE_DRAWINGS_DIFF'].astype(int)

    del cc_bal['BALANCE_LEFT']
    del cc_bal['total_drawings']
    del cc_bal['DRAWINGS_POS']
    del cc_bal['DRAWINGS_OTHER']
    del cc_bal['DRAWINGS_ATM']
    del cc_bal['DRAWINGS_CURRENT']
    
    #lets see the total number of times credit has been drawn
    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['CNT_DRAWINGS_ATM_CURRENT'].sum().reset_index().rename(index = str, columns = {'CNT_DRAWINGS_ATM_CURRENT' : 'ATM'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['CNT_DRAWINGS_CURRENT'].sum().reset_index().rename(index = str, columns = {'CNT_DRAWINGS_CURRENT' : 'CURRENT'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['CNT_DRAWINGS_OTHER_CURRENT'].sum().reset_index().rename(index = str, columns = {'CNT_DRAWINGS_OTHER_CURRENT' : 'OTHER'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['CNT_DRAWINGS_POS_CURRENT'].sum().reset_index().rename(index = str, columns = {'CNT_DRAWINGS_POS_CURRENT' : 'POS'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    cc_bal['TOTAL_DRAWINGS'] = cc_bal['ATM'] + cc_bal['CURRENT'] + cc_bal['OTHER'] + cc_bal['POS']

    del cc_bal['ATM']
    del cc_bal['CURRENT']
    del cc_bal['OTHER']
    del cc_bal['POS']
    
    #cc_bal.drop('AMT_RECIVABLE', axis=1, inplace=True) #same column as AMT_TOTAL_RECEIVABLE
    
    
    #Finding out the receivable amount from bad debts
    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_TOTAL_RECEIVABLE'].sum().reset_index().rename(index = str, columns = {'AMT_TOTAL_RECEIVABLE' : 'R_TOTAL'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_RECEIVABLE_PRINCIPAL'].sum().reset_index().rename(index = str, columns = {'AMT_RECEIVABLE_PRINCIPAL' : 'R_PRIN'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    cc_bal['R_BAD_DEBTS'] = cc_bal['R_TOTAL'] - cc_bal['R_PRIN']
    del cc_bal['R_TOTAL']
    del cc_bal['R_PRIN']
    
    
    print("Done with credit_card_balance feature engg")
    
    print("START OF MIN MAX FEATURES")
    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_BALANCE'].max().reset_index().rename(index = str, columns ={ 'AMT_BALANCE' : 'MAX_BAL'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_BALANCE'].min().reset_index().rename(index = str, columns ={ 'AMT_BALANCE' : 'MIN_BAL'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_CREDIT_LIMIT_ACTUAL'].max().reset_index().rename(index = str, columns ={ 'AMT_CREDIT_LIMIT_ACTUAL' : 'MAX_LIM'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_CREDIT_LIMIT_ACTUAL'].min().reset_index().rename(index = str, columns ={ 'AMT_CREDIT_LIMIT_ACTUAL' : 'MIN_LIM'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_DRAWINGS_ATM_CURRENT'].max().reset_index().rename(index = str, columns ={ 'AMT_DRAWINGS_ATM_CURRENT' : 'MAX_DRW'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_DRAWINGS_ATM_CURRENT'].min().reset_index().rename(index = str, columns ={ 'AMT_DRAWINGS_ATM_CURRENT' : 'MIN_DRW'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_DRAWINGS_CURRENT'].max().reset_index().rename(index = str, columns ={ 'AMT_DRAWINGS_CURRENT' : 'MAX_C_DRW'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_DRAWINGS_CURRENT'].min().reset_index().rename(index = str, columns ={ 'AMT_DRAWINGS_CURRENT' : 'MIN_C_DRW'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_DRAWINGS_OTHER_CURRENT'].max().reset_index().rename(index = str, columns ={ 'AMT_DRAWINGS_OTHER_CURRENT' : 'MAX_O_DRW'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_DRAWINGS_OTHER_CURRENT'].min().reset_index().rename(index = str, columns ={ 'AMT_DRAWINGS_OTHER_CURRENT' : 'MIN_O_DRW'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_DRAWINGS_POS_CURRENT'].max().reset_index().rename(index = str, columns ={ 'AMT_DRAWINGS_POS_CURRENT' : 'MAX_P_DRW'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_DRAWINGS_POS_CURRENT'].min().reset_index().rename(index = str, columns ={ 'AMT_DRAWINGS_POS_CURRENT' : 'MIN_P_DRW'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_INST_MIN_REGULARITY'].max().reset_index().rename(index = str, columns ={ 'AMT_INST_MIN_REGULARITY' : 'MAX_INST'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_INST_MIN_REGULARITY'].min().reset_index().rename(index = str, columns ={ 'AMT_INST_MIN_REGULARITY' : 'MIN_INST'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_PAYMENT_CURRENT'].max().reset_index().rename(index = str, columns ={ 'AMT_PAYMENT_CURRENT' : 'MAX_PAYMENT'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_PAYMENT_CURRENT'].min().reset_index().rename(index = str, columns ={ 'AMT_PAYMENT_CURRENT' : 'MIN_PAYMENT'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_RECEIVABLE_PRINCIPAL'].max().reset_index().rename(index = str, columns ={ 'AMT_RECEIVABLE_PRINCIPAL' : 'MAX_PRIN'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_RECEIVABLE_PRINCIPAL'].min().reset_index().rename(index = str, columns ={ 'AMT_RECEIVABLE_PRINCIPAL' : 'MIN_PRIN'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_RECIVABLE'].max().reset_index().rename(index = str, columns ={ 'AMT_RECIVABLE' : 'AMT_R_MAX'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['AMT_RECIVABLE'].min().reset_index().rename(index = str, columns ={ 'AMT_RECIVABLE' : 'AMT_R_MIN'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['CNT_INSTALMENT_MATURE_CUM'].max().reset_index().rename(index = str, columns ={ 'CNT_INSTALMENT_MATURE_CUM' : 'CNT_INSTL_MAX'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = cc_bal.groupby(by = ['SK_ID_CURR'])['CNT_INSTALMENT_MATURE_CUM'].min().reset_index().rename(index = str, columns ={ 'CNT_INSTALMENT_MATURE_CUM' : 'CNT_INSTL_MIN'})
    cc_bal = cc_bal.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    
    nb_prevs = cc_bal[['SK_ID_CURR', 'SK_ID_PREV']].groupby('SK_ID_CURR').count()
    cc_bal['SK_ID_PREV'] = cc_bal['SK_ID_CURR'].map(nb_prevs['SK_ID_PREV'])
    
    print('Compute weighted average for credit card balance')
    wm = lambda x: np.average(x, weights=-1/cc_bal.loc[x.index, 'MONTHS_BALANCE'])
    #avg_cc_bal = cc_bal.groupby('SK_ID_CURR').agg(wm) 
    #avg_cc_bal = cc_bal.groupby('SK_ID_CURR').mean()
    f = {'AMT_BALANCE': wm, 'AMT_CREDIT_LIMIT_ACTUAL': wm, 'SK_DPD': wm, 'SK_DPD_DEF':wm, 'AMT_DRAWINGS_ATM_CURRENT':wm, 'AMT_DRAWINGS_CURRENT':wm,
            'AMT_DRAWINGS_OTHER_CURRENT':wm, 'AMT_DRAWINGS_POS_CURRENT':wm, 'AMT_INST_MIN_REGULARITY':wm, 'AMT_PAYMENT_CURRENT':wm,
            'AMT_PAYMENT_TOTAL_CURRENT':wm, 'AMT_RECEIVABLE_PRINCIPAL':wm, 'AMT_RECIVABLE':wm, 'AMT_TOTAL_RECEIVABLE':wm,
            'CNT_INSTALMENT_MATURE_CUM':wm}
    
    
    avg_cc_bal = cc_bal.groupby('SK_ID_CURR')['AMT_BALANCE', 'AMT_CREDIT_LIMIT_ACTUAL', 'SK_DPD', 'SK_DPD_DEF', 'AMT_DRAWINGS_ATM_CURRENT', 'AMT_DRAWINGS_CURRENT',
            'AMT_DRAWINGS_OTHER_CURRENT', 'AMT_DRAWINGS_POS_CURRENT', 'AMT_INST_MIN_REGULARITY', 'AMT_PAYMENT_CURRENT',
            'AMT_PAYMENT_TOTAL_CURRENT', 'AMT_RECEIVABLE_PRINCIPAL', 'AMT_RECIVABLE', 'AMT_TOTAL_RECEIVABLE',
            'CNT_INSTALMENT_MATURE_CUM'].agg(f)
                                               
    avg_cc_bal.columns = ['cc_bal_' + f_ for f_ in avg_cc_bal.columns]
    print("weighted avgs added")                                           
                                               
    
    del cc_bal, nb_prevs
    gc.collect()
    
    print('Reading Installments')
    ip = pd.read_csv('../input/installments_payments.csv')
    
    #Feature engg for installments_payments 
    #Difference of instalment paid and actual instalment(for installments_payments.csv)
    grp = ip.groupby(by = ['SK_ID_CURR'])['AMT_INSTALMENT'].sum().reset_index().rename(index = str, columns = {'AMT_INSTALMENT' : 'AMT_INSTALMENT_ACTUAL'})
    ip = ip.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    grp = ip.groupby(by = ['SK_ID_CURR'])['AMT_PAYMENT'].sum().reset_index().rename(index = str, columns = {'AMT_PAYMENT' : 'AMT_PAYMENT_DONE'})
    ip = ip.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    ip['DIFF'] = (ip['AMT_INSTALMENT_ACTUAL'] - ip['AMT_PAYMENT_DONE'])
    del ip['AMT_INSTALMENT_ACTUAL']
    del ip['AMT_PAYMENT_DONE']
    gc.collect()

    grp = ip.groupby(by = ['SK_ID_CURR'])['DIFF'].mean().reset_index().rename(index = str, columns ={ 'DIFF' : 'DIFF_INSTALMENT_PAYMENT'})
    ip = ip.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    del ip['DIFF']
    gc.collect()
    
    #Difference between DAYS_INSTALMENT and DAYS_ENTRY_PAYMENT
    ip['DAYS_INSTALMENT'] = abs(ip['DAYS_INSTALMENT'])
    ip['DAYS_ENTRY_PAYMENT'] = abs(ip['DAYS_ENTRY_PAYMENT'])
    grp = ip.groupby(by = ['SK_ID_CURR'])['DAYS_INSTALMENT'].sum().reset_index().rename(index = str, columns = {'DAYS_INSTALMENT' : 'DAYS_INSTALMENT_DATE'})
    ip = ip.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    grp = ip.groupby(by = ['SK_ID_CURR'])['DAYS_ENTRY_PAYMENT'].sum().reset_index().rename(index = str, columns = {'DAYS_ENTRY_PAYMENT' : 'DAYS_PAYMENT_DONE'})
    ip = ip.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp
    gc.collect()

    ip['DIFF'] = (ip['DAYS_PAYMENT_DONE'] - ip['DAYS_INSTALMENT_DATE'])
    del ip['DAYS_INSTALMENT_DATE']
    del ip['DAYS_PAYMENT_DONE']
    gc.collect()

    grp = ip.groupby(by = ['SK_ID_CURR'])['DIFF'].max().reset_index().rename(index = str, columns ={ 'DIFF' : 'DIFF_INSTALMENT_PAYMENT_DATE_MAX'})
    ip = ip.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = ip.groupby(by = ['SK_ID_CURR'])['DIFF'].min().reset_index().rename(index = str, columns ={ 'DIFF' : 'DIFF_INSTALMENT_PAYMENT_DATE_MIN'})
    ip = ip.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    grp = ip.groupby(by = ['SK_ID_CURR'])['NUM_INSTALMENT_NUMBER'].min().reset_index().rename(index = str, columns ={ 'NUM_INSTALMENT_NUMBER' : 'MIN_INSTAL_NUM'})
    ip = ip.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect
    
    grp = ip.groupby(by = ['SK_ID_CURR'])['NUM_INSTALMENT_NUMBER'].max().reset_index().rename(index = str, columns ={ 'NUM_INSTALMENT_NUMBER' : 'MAX_INSTAL_NUM'})
    ip = ip.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = ip.groupby(by = ['SK_ID_CURR'])['AMT_INSTALMENT'].min().reset_index().rename(index = str, columns ={ 'AMT_INSTALMENT' : 'MIN_INSTAL_AMT'})
    ip = ip.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = ip.groupby(by = ['SK_ID_CURR'])['AMT_INSTALMENT'].max().reset_index().rename(index = str, columns ={ 'AMT_INSTALMENT' : 'MAX_INSTAL_AMT'})
    ip = ip.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = ip.groupby(by = ['SK_ID_CURR'])['AMT_PAYMENT'].min().reset_index().rename(index = str, columns ={ 'AMT_PAYMENT' : 'MIN_PAY_AMT'})
    ip = ip.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()
    
    grp = ip.groupby(by = ['SK_ID_CURR'])['AMT_PAYMENT'].max().reset_index().rename(index = str, columns ={ 'AMT_PAYMENT' : 'MAX_PAY_AMT'})
    ip = ip.merge(grp, on = ['SK_ID_CURR'], how = 'left')
    del grp 
    gc.collect()

    del ip['DIFF']
    gc.collect() 
    
    
    nb_prevs = ip[['SK_ID_CURR', 'SK_ID_PREV']].groupby('SK_ID_CURR').count()
    ip['SK_ID_PREV'] = ip['SK_ID_CURR'].map(nb_prevs['SK_ID_PREV'])
    
    avg_inst = ip.groupby('SK_ID_CURR').mean()
    avg_inst.columns = ['inst_' + f_ for f_ in avg_inst.columns]
    
    del ip, nb_prevs
    
    print('done processing other tables, moving to application')
    
    print('Reading data and test')
    application_train = pd.read_csv('../input/application_train.csv')
    application_test = pd.read_csv('../input/application_test.csv')
    
    application_train['CODE_GENDER'].replace('XNA',np.nan, inplace=True)
    application_train['DAYS_LAST_PHONE_CHANGE'].replace(0, np.nan, inplace=True)
    application_train['DAYS_EMPLOYED'].replace(365243,np.nan, inplace=True)
    
    application_test['CODE_GENDER'].replace('XNA',np.nan, inplace=True)
    application_test['DAYS_LAST_PHONE_CHANGE'].replace(0, np.nan, inplace=True)
    application_test['DAYS_EMPLOYED'].replace(365243,np.nan, inplace=True)
    
    application_train['income_credit_percentage'] = application_train['AMT_INCOME_TOTAL'] / application_train['AMT_CREDIT']
    application_train['income_per_child'] = application_train['AMT_INCOME_TOTAL'] / (1 + application_train['CNT_CHILDREN'])
    application_train['phone_to_birth_ratio'] = application_train['DAYS_LAST_PHONE_CHANGE'] / application_train['DAYS_BIRTH']
    application_train['phone_to_employ_ratio'] = application_train['DAYS_LAST_PHONE_CHANGE'] / application_train['DAYS_EMPLOYED']
    application_train['car_to_birth_ratio'] = application_train['OWN_CAR_AGE'] / application_train['DAYS_BIRTH']
    application_train['car_to_employ_ratio'] = application_train['OWN_CAR_AGE'] / application_train['DAYS_EMPLOYED']
    application_train['long_employment'] = (application_train['DAYS_EMPLOYED'] < -2000).astype(int)
    application_train['retirement_age'] = (application_train['DAYS_BIRTH'] < -14000).astype(int)

    application_train['cnt_non_child'] = application_train['CNT_FAM_MEMBERS'] - application_train['CNT_CHILDREN']
    application_train['child_to_non_child_ratio'] = application_train['CNT_CHILDREN'] / application_train['cnt_non_child']
    application_train['income_per_non_child'] = application_train['AMT_INCOME_TOTAL'] / application_train['cnt_non_child']
    application_train['credit_per_person'] = application_train['AMT_CREDIT'] / application_train['CNT_FAM_MEMBERS']
    application_train['credit_per_child'] = application_train['AMT_CREDIT'] / (1 + application_train['CNT_CHILDREN'])
    application_train['credit_per_non_child'] = application_train['AMT_CREDIT'] / application_train['cnt_non_child']
    
    
    application_test['income_credit_percentage'] = application_test['AMT_INCOME_TOTAL'] / application_test['AMT_CREDIT']
    application_test['income_per_child'] = application_test['AMT_INCOME_TOTAL'] / (1 + application_test['CNT_CHILDREN'])
    application_test['phone_to_birth_ratio'] = application_test['DAYS_LAST_PHONE_CHANGE'] / application_test['DAYS_BIRTH']
    application_test['phone_to_employ_ratio'] = application_test['DAYS_LAST_PHONE_CHANGE'] / application_test['DAYS_EMPLOYED']
    application_test['car_to_birth_ratio'] = application_test['OWN_CAR_AGE'] / application_test['DAYS_BIRTH']
    application_test['car_to_employ_ratio'] = application_test['OWN_CAR_AGE'] / application_test['DAYS_EMPLOYED']
    application_test['long_employment'] = (application_test['DAYS_EMPLOYED'] < -2000).astype(int)
    application_test['retirement_age'] = (application_test['DAYS_BIRTH'] < -14000).astype(int)

    application_test['cnt_non_child'] = application_test['CNT_FAM_MEMBERS'] - application_test['CNT_CHILDREN']
    application_test['child_to_non_child_ratio'] = application_test['CNT_CHILDREN'] / application_test['cnt_non_child']
    application_test['income_per_non_child'] = application_test['AMT_INCOME_TOTAL'] / application_test['cnt_non_child']
    application_test['credit_per_person'] = application_test['AMT_CREDIT'] / application_test['CNT_FAM_MEMBERS']
    application_test['credit_per_child'] = application_test['AMT_CREDIT'] / (1 + application_test['CNT_CHILDREN'])
    application_test['credit_per_non_child'] = application_test['AMT_CREDIT'] / application_test['cnt_non_child']
    
    application_train['external_sources_weighted'] = application_train.EXT_SOURCE_1 * 2 + application_train.EXT_SOURCE_2 * 3 + application_train.EXT_SOURCE_3 * 4
    application_test['external_sources_weighted'] = application_test.EXT_SOURCE_1 * 2 + application_test.EXT_SOURCE_2 * 3 + application_test.EXT_SOURCE_3 * 4
    
    AGGREGATION_RECIPIES = [
    (['CODE_GENDER', 'NAME_EDUCATION_TYPE'], [('AMT_ANNUITY', 'max'),
                                              ('AMT_CREDIT', 'max'),
                                              ('EXT_SOURCE_1', 'mean'),
                                              ('EXT_SOURCE_2', 'mean'),
                                              ('OWN_CAR_AGE', 'max'),
                                              ('OWN_CAR_AGE', 'sum')]),
    (['CODE_GENDER', 'ORGANIZATION_TYPE'], [('AMT_ANNUITY', 'mean'),
                                            ('AMT_INCOME_TOTAL', 'mean'),
                                            ('DAYS_REGISTRATION', 'mean'),
                                            ('EXT_SOURCE_1', 'mean')]),
    (['CODE_GENDER', 'REG_CITY_NOT_WORK_CITY'], [('AMT_ANNUITY', 'mean'),
                                                 ('CNT_CHILDREN', 'mean'),
                                                 ('DAYS_ID_PUBLISH', 'mean')]),
    (['CODE_GENDER', 'NAME_EDUCATION_TYPE', 'OCCUPATION_TYPE', 'REG_CITY_NOT_WORK_CITY'], [('EXT_SOURCE_1', 'mean'),
                                                                                           ('EXT_SOURCE_2', 'mean')]),
    (['NAME_EDUCATION_TYPE', 'OCCUPATION_TYPE'], [('AMT_CREDIT', 'mean'),
                                                  ('AMT_REQ_CREDIT_BUREAU_YEAR', 'mean'),
                                                  ('APARTMENTS_AVG', 'mean'),
                                                  ('BASEMENTAREA_AVG', 'mean'),
                                                  ('EXT_SOURCE_1', 'mean'),
                                                  ('EXT_SOURCE_2', 'mean'),
                                                  ('EXT_SOURCE_3', 'mean'),
                                                  ('NONLIVINGAREA_AVG', 'mean'),
                                                  ('OWN_CAR_AGE', 'mean'),
                                                  ('YEARS_BUILD_AVG', 'mean')]),
    (['NAME_EDUCATION_TYPE', 'OCCUPATION_TYPE', 'REG_CITY_NOT_WORK_CITY'], [('ELEVATORS_AVG', 'mean'),
                                                                            ('EXT_SOURCE_1', 'mean')]),
    (['OCCUPATION_TYPE'], [('AMT_ANNUITY', 'mean'),
                           ('CNT_CHILDREN', 'mean'),
                           ('CNT_FAM_MEMBERS', 'mean'),
                           ('DAYS_BIRTH', 'mean'),
                           ('DAYS_EMPLOYED', 'mean'),
                           ('DAYS_ID_PUBLISH', 'mean'),
                           ('DAYS_REGISTRATION', 'mean'),
                           ('EXT_SOURCE_1', 'mean'),
                           ('EXT_SOURCE_2', 'mean'),
                           ('EXT_SOURCE_3', 'mean')]),
    ]
    
    groupby_aggregate_names = []
    for groupby_cols, specs in tqdm(AGGREGATION_RECIPIES):
        group_object = application_train.groupby(groupby_cols)
        for select, agg in tqdm(specs):
            groupby_aggregate_name = '{}_{}_{}'.format('_'.join(groupby_cols), agg, select)
            application_train = application_train.merge(group_object[select]
                                  .agg(agg)
                                  .reset_index()
                                  .rename(index=str,
                                          columns={select: groupby_aggregate_name})
                                  [groupby_cols + [groupby_aggregate_name]],
                                  on=groupby_cols,
                                  how='left')
            groupby_aggregate_names.append(groupby_aggregate_name)
            
            
    X_agg = application_train[groupby_aggregate_names + ['SK_ID_CURR']]
    application_train = application_train.merge(X_agg, on='SK_ID_CURR', how='left')
    
    groupby_aggregate_names = []
    for groupby_cols, specs in tqdm(AGGREGATION_RECIPIES):
        group_object = application_test.groupby(groupby_cols)
        for select, agg in tqdm(specs):
            groupby_aggregate_name = '{}_{}_{}'.format('_'.join(groupby_cols), agg, select)
            application_test = application_test.merge(group_object[select]
                                  .agg(agg)
                                  .reset_index()
                                  .rename(index=str,
                                          columns={select: groupby_aggregate_name})
                                  [groupby_cols + [groupby_aggregate_name]],
                                  on=groupby_cols,
                                  how='left')
            groupby_aggregate_names.append(groupby_aggregate_name)
            
            
    X_agg1 = application_test[groupby_aggregate_names + ['SK_ID_CURR']]
    application_test = application_test.merge(X_agg1, on='SK_ID_CURR', how='left')
    
    
    diff_feature_names = []
    for groupby_cols, specs in tqdm(AGGREGATION_RECIPIES):
        for select, agg in tqdm(specs):
            if agg in ['mean','median','max','min']:
                groupby_aggregate_name = '{}_{}_{}'.format('_'.join(groupby_cols), agg, select)
                diff_name = '{}_diff'.format(groupby_aggregate_name)
                abs_diff_name = '{}_abs_diff'.format(groupby_aggregate_name)
    
                application_train[diff_name] = application_train[select] - application_train[groupby_aggregate_name] 
                application_train[abs_diff_name] = np.abs(application_train[select] - application_train[groupby_aggregate_name]) 
    
                diff_feature_names.append(diff_name)
                diff_feature_names.append(abs_diff_name)
                
    X_diff = application_train[diff_feature_names + ['SK_ID_CURR']]
    application_train = application_train.merge(X_diff, on='SK_ID_CURR', how='left')
    
    
    diff_feature_names = []
    for groupby_cols, specs in tqdm(AGGREGATION_RECIPIES):
        for select, agg in tqdm(specs):
            if agg in ['mean','median','max','min']:
                groupby_aggregate_name = '{}_{}_{}'.format('_'.join(groupby_cols), agg, select)
                diff_name = '{}_diff'.format(groupby_aggregate_name)
                abs_diff_name = '{}_abs_diff'.format(groupby_aggregate_name)
    
                application_test[diff_name] = application_test[select] - application_test[groupby_aggregate_name] 
                application_test[abs_diff_name] = np.abs(application_test[select] - application_test[groupby_aggregate_name]) 
    
                diff_feature_names.append(diff_name)
                diff_feature_names.append(abs_diff_name)
                
    X_diff1 = application_test[diff_feature_names + ['SK_ID_CURR']]
    application_test = application_test.merge(X_diff1, on='SK_ID_CURR', how='left')
    
    print("Done with agg features")
    
    print('Shapes : ', application_train.shape, application_test.shape)
    
    y = application_train['TARGET']
    del application_train['TARGET']
    
    categorical_feats = [
        f for f in application_train.columns if application_train[f].dtype == 'object'
    ]
    categorical_feats
    for f_ in categorical_feats:
        application_train[f_], indexer = pd.factorize(application_train[f_])
        application_test[f_] = indexer.get_indexer(application_test[f_])
        
    median_ext1 = application_train.groupby(['NAME_INCOME_TYPE'])["EXT_SOURCE_1"].median()

    def fillna_ext1(row, median_ext1):
        ext1 = median_ext1.loc[row["NAME_INCOME_TYPE"]]
        return ext1
    application_train["EXT_SOURCE_1"] = application_train.apply(lambda row : fillna_ext1(row, median_ext1) if np.isnan(row['EXT_SOURCE_1']) else row['EXT_SOURCE_1'], axis=1)
    
    median_ext2 = application_train.groupby(['NAME_INCOME_TYPE'])["EXT_SOURCE_2"].median()

    def fillna_ext2(row, median_ext2):
        ext2 = median_ext2.loc[row["NAME_INCOME_TYPE"]]
        return ext2
    application_train["EXT_SOURCE_2"] = application_train.apply(lambda row : fillna_ext2(row, median_ext2) if np.isnan(row['EXT_SOURCE_2']) else row['EXT_SOURCE_2'], axis=1)
    
    median_ext3 = application_train.groupby(['NAME_INCOME_TYPE'])["EXT_SOURCE_3"].median()

    def fillna_ext3(row, median_ext3):
        ext3 = median_ext3.loc[row["NAME_INCOME_TYPE"]]
        return ext3
    application_train["EXT_SOURCE_3"] = application_train.apply(lambda row : fillna_ext3(row, median_ext3) if np.isnan(row['EXT_SOURCE_3']) else row['EXT_SOURCE_3'], axis=1)
    
    median_ext4 = application_test.groupby(['NAME_INCOME_TYPE'])["EXT_SOURCE_3"].median()

    def fillna_ext4(row, median_ext4):
        ext4 = median_ext4.loc[row["NAME_INCOME_TYPE"]]
        return ext4
    application_test["EXT_SOURCE_3"] = application_test.apply(lambda row : fillna_ext4(row, median_ext4) if np.isnan(row['EXT_SOURCE_3']) else row['EXT_SOURCE_3'], axis=1)
    
    median_ext5 = application_test.groupby(['NAME_INCOME_TYPE'])["EXT_SOURCE_2"].median()

    def fillna_ext5(row, median_ext5):
        ext5 = median_ext5.loc[row["NAME_INCOME_TYPE"]]
        return ext5
    application_test["EXT_SOURCE_2"] = application_test.apply(lambda row : fillna_ext5(row, median_ext5) if np.isnan(row['EXT_SOURCE_2']) else row['EXT_SOURCE_2'], axis=1)
    
    median_ext6 = application_test.groupby(['NAME_INCOME_TYPE'])["EXT_SOURCE_1"].median()

    def fillna_ext6(row, median_ext6):
        ext6 = median_ext6.loc[row["NAME_INCOME_TYPE"]]
        return ext6
    application_test["EXT_SOURCE_1"] = application_test.apply(lambda row : fillna_ext6(row, median_ext6) if np.isnan(row['EXT_SOURCE_1']) else row['EXT_SOURCE_1'], axis=1)
    
    print("Done filling the missing values in ext_source")
    
    application_train['EXT_SOURCE_3'].fillna((application_train['EXT_SOURCE_3'].mean()), inplace=True)
    application_test['EXT_SOURCE_3'].fillna((application_test['EXT_SOURCE_3'].mean()), inplace=True)
    
    application_train['EXT_12'] = application_train['EXT_SOURCE_1']*application_train['EXT_SOURCE_2']
    application_train['EXT_23'] = application_train['EXT_SOURCE_2']*application_train['EXT_SOURCE_3']
    application_train['EXT_31'] = application_train['EXT_SOURCE_1']*application_train['EXT_SOURCE_3']
    
    col = application_train.loc[:, "EXT_SOURCE_1":"EXT_SOURCE_3"]
    application_train['AVG_EXT'] = col.mean(axis=1)
    
    application_test['EXT_12'] = application_test['EXT_SOURCE_1']*application_test['EXT_SOURCE_2']
    application_test['EXT_23'] = application_test['EXT_SOURCE_2']*application_test['EXT_SOURCE_3']
    application_test['EXT_31'] = application_test['EXT_SOURCE_1']*application_test['EXT_SOURCE_3']
    
    col = application_test.loc[:, "EXT_SOURCE_1":"EXT_SOURCE_3"]
    application_test['AVG_EXT'] = col.mean(axis=1)
    
    print('done with ext_source')
    
    median_annuity = application_train.groupby(['CODE_GENDER'])['AMT_ANNUITY'].median()

    def fillna_ann(row, median_annuity):
        ann = median_annuity.loc[row["CODE_GENDER"]]
        return ann
    application_train["AMT_ANNUITY"] = application_train.apply(lambda row : fillna_ann(row, median_annuity) if np.isnan(row['AMT_ANNUITY']) else row['AMT_ANNUITY'], axis=1)
    
    median_annuity1 = application_test.groupby(['CODE_GENDER'])['AMT_ANNUITY'].median()

    def fillna_ann1(row, median_annuity1):
        ann1 = median_annuity1.loc[row["CODE_GENDER"]]
        return ann1
    application_test["AMT_ANNUITY"] = application_test.apply(lambda row : fillna_ann1(row, median_annuity1) if np.isnan(row['AMT_ANNUITY']) else row['AMT_ANNUITY'], axis=1)
    
    application_train['BIRTH_CREDIT'] = application_train['AMT_CREDIT']*application_train['DAYS_BIRTH']
    application_train['BIRTH_ANNUITY'] = application_train['AMT_ANNUITY']*application_train['DAYS_BIRTH']

    application_test['BIRTH_ANNUITY'] = application_test['AMT_ANNUITY']*application_test['DAYS_BIRTH']
    application_test['BIRTH_CREDIT'] = application_test['AMT_CREDIT']*application_test['DAYS_BIRTH']

    application_train['COST_INCOME'] = application_train['AMT_INCOME_TOTAL'] - application_train['AMT_GOODS_PRICE']
    application_test['COST_INCOME'] = application_test['AMT_INCOME_TOTAL'] - application_test['AMT_GOODS_PRICE']
    
    application_test['Ratio_of_debt_credit'] = ((application_test['AMT_ANNUITY']+application_test['AMT_INCOME_TOTAL']) - (application_test['AMT_GOODS_PRICE']))/application_test['AMT_CREDIT']
    application_train['Ratio_of_debt_credit'] = ((application_train['AMT_ANNUITY']+application_train['AMT_INCOME_TOTAL']) - (application_train['AMT_GOODS_PRICE']))/application_train['AMT_CREDIT']
    
    application_train['Ratio_of_credit_employement'] = application_train['AMT_CREDIT']/application_train['DAYS_EMPLOYED']
    application_test['Ratio_of_credit_employement'] = application_test['AMT_CREDIT']/application_test['DAYS_EMPLOYED']
    application_test['income_debt_ratio'] = ((application_test['AMT_ANNUITY']+application_test['AMT_INCOME_TOTAL']) - (application_test['AMT_GOODS_PRICE'])) / (application_test['AMT_ANNUITY']+application_test['AMT_INCOME_TOTAL'])
    application_train['income_debt_ratio'] = ((application_train['AMT_ANNUITY']+application_train['AMT_INCOME_TOTAL']) - (application_train['AMT_GOODS_PRICE'])) / (application_train['AMT_ANNUITY']+application_train['AMT_INCOME_TOTAL'])

    application_train['CreditEmployPdt'] = application_train['AMT_CREDIT'] * application_train['DAYS_EMPLOYED']
    application_test['CreditEmployPdt'] = application_test['AMT_CREDIT'] * application_test['DAYS_EMPLOYED']

    application_train['DAYS_EMPLOYED_PERC'] = application_train['DAYS_EMPLOYED'] / application_train['DAYS_BIRTH']
    application_train['INCOME_CREDIT_PERC'] = application_train['AMT_INCOME_TOTAL'] / application_train['AMT_CREDIT']
    application_train['INCOME_PER_PERSON'] = application_train['AMT_INCOME_TOTAL'] / application_train['CNT_FAM_MEMBERS']
    application_train['ANNUITY_INCOME_PERC'] = application_train['AMT_ANNUITY'] / application_train['AMT_INCOME_TOTAL']
    application_train['ANN_CREDIT_RATIO'] = application_train['AMT_ANNUITY'] / application_train['AMT_CREDIT']
    application_train['CHILD_RATIO'] = application_train['CNT_CHILDREN'] / application_train['CNT_FAM_MEMBERS']

    application_test['DAYS_EMPLOYED_PERC'] = application_test['DAYS_EMPLOYED'] / application_test['DAYS_BIRTH']
    application_test['INCOME_CREDIT_PERC'] = application_test['AMT_INCOME_TOTAL'] / application_test['AMT_CREDIT']
    application_test['INCOME_PER_PERSON'] = application_test['AMT_INCOME_TOTAL'] / application_test['CNT_FAM_MEMBERS']
    application_test['ANNUITY_INCOME_PERC'] = application_test['AMT_ANNUITY'] / application_test['AMT_INCOME_TOTAL']
    application_test['ANN_CREDIT_RATIO'] = application_test['AMT_ANNUITY'] / application_test['AMT_CREDIT']
    application_test['CHILD_RATIO'] = application_test['CNT_CHILDREN'] / application_test['CNT_FAM_MEMBERS']

    application_train['DAYS_UNEMPLOYED_PERC'] = 1 - application_train['DAYS_EMPLOYED_PERC']
    application_test['DAYS_UNEMPLOYED_PERC'] = 1 - application_test['DAYS_EMPLOYED_PERC']
    application_train['DAYS_UNEMPLOYED'] = application_train['DAYS_BIRTH'] - application_train['DAYS_EMPLOYED']
    application_test['DAYS_UNEMPLOYED'] = application_test['DAYS_BIRTH'] - application_test['DAYS_EMPLOYED']
    
    application_train['AGE'] = application_train['DAYS_BIRTH'] / (365)
    application_test['AGE'] = application_test['DAYS_BIRTH'] / (365)
    
    application_train['AGE'] = application_train['AGE'].astype(int)
    application_test['AGE'] = application_test['AGE'].astype(int)
    
    application_train['AGE'] = abs(application_train['AGE'])
    application_test['AGE'] = abs(application_test['AGE'])
    
    application_train['0-25'] = np.where(application_train['AGE'] <= 25, 1, 0)
    application_train['25-35'] = np.where((application_train['AGE'] <= 35) & (application_train['AGE'] >= 25), 1, 0)
    application_train['35-50'] = np.where((application_train['AGE'] <= 50) & (application_train['AGE'] >= 35), 1, 0)
    application_train['50-70'] = np.where((application_train['AGE'] <= 70) & (application_train['AGE'] >= 50), 1, 0) 
    
    application_test['0-25'] = np.where(application_test['AGE'] <= 25, 1, 0)
    application_test['25-35'] = np.where((application_test['AGE'] <= 35) & (application_test['AGE'] >= 25), 1, 0)
    application_test['35-50'] = np.where((application_test['AGE'] <= 50) & (application_test['AGE'] >= 35), 1, 0)
    application_test['50-70'] = np.where((application_test['AGE'] <= 70) & (application_test['AGE'] >= 50), 1, 0)
    
    application_train['TOTAL_NO_ENQUIRY'] = application_train['AMT_REQ_CREDIT_BUREAU_HOUR'] + application_train['AMT_REQ_CREDIT_BUREAU_DAY'] + application_train['AMT_REQ_CREDIT_BUREAU_WEEK'] + application_train['AMT_REQ_CREDIT_BUREAU_MON'] + application_train['AMT_REQ_CREDIT_BUREAU_QRT'] + application_train['AMT_REQ_CREDIT_BUREAU_YEAR']
    application_test['TOTAL_NO_ENQUIRY'] = application_test['AMT_REQ_CREDIT_BUREAU_HOUR'] + application_test['AMT_REQ_CREDIT_BUREAU_DAY'] + application_test['AMT_REQ_CREDIT_BUREAU_WEEK'] + application_test['AMT_REQ_CREDIT_BUREAU_MON'] + application_test['AMT_REQ_CREDIT_BUREAU_QRT'] + application_test['AMT_REQ_CREDIT_BUREAU_YEAR']                      
    
    application_train['TOT_SOC_OBS_DPD'] = application_train['OBS_30_CNT_SOCIAL_CIRCLE'] + application_train['OBS_60_CNT_SOCIAL_CIRCLE']
    application_test['TOT_SOC_OBS_DPD'] = application_test['OBS_30_CNT_SOCIAL_CIRCLE'] + application_test['OBS_60_CNT_SOCIAL_CIRCLE']
    
    application_train['TOT_SOC_DEF_DPD'] = application_train['DEF_30_CNT_SOCIAL_CIRCLE'] + application_train['DEF_60_CNT_SOCIAL_CIRCLE']
    application_test['TOT_SOC_DEF_DPD'] = application_test['DEF_30_CNT_SOCIAL_CIRCLE'] + application_test['DEF_60_CNT_SOCIAL_CIRCLE']
    
    application_train['CAR_AGE_RATIO'] = application_train['OWN_CAR_AGE'] / abs(application_train['AGE'].astype(int))
    application_test['CAR_AGE_RATIO'] = application_test['OWN_CAR_AGE'] / abs(application_test['AGE'].astype(int))
    
    application_train['ANNUITY_LENGTH'] = application_train['AMT_CREDIT'] / application_train['AMT_ANNUITY']
    application_test['ANNUITY_LENGTH'] = application_test['AMT_CREDIT'] / application_test['AMT_ANNUITY']

    application_train['CHILDREN_RATIO'] = application_train['CNT_CHILDREN'] / application_train['CNT_FAM_MEMBERS']
    application_test['CHILDREN_RATIO'] = application_test['CNT_CHILDREN'] / application_test['CNT_FAM_MEMBERS']

    application_train['ANNUITY_EMPLOYED_RATIO'] = application_train['ANNUITY_LENGTH'] / application_train['DAYS_EMPLOYED']
    application_test['ANNUITY_EMPLOYED_RATIO'] = application_test['ANNUITY_LENGTH'] / application_test['DAYS_EMPLOYED']
    
    #calculating the total no of documents submitted
    application_train['TOTAL_DOCS_SUBMITTED'] = application_train.loc[:, application_train.columns.str.contains('FLAG_DOCUMENT')].sum(axis=1)
    application_test['TOTAL_DOCS_SUBMITTED'] = application_test.loc[:, application_test.columns.str.contains('FLAG_DOCUMENT')].sum(axis=1)
    
    application_train.drop(['FLAG_DOCUMENT_2','FLAG_DOCUMENT_4','FLAG_DOCUMENT_7','FLAG_DOCUMENT_10','FLAG_DOCUMENT_12',
                            'FLAG_DOCUMENT_14','FLAG_DOCUMENT_15','FLAG_DOCUMENT_17','FLAG_DOCUMENT_19','FLAG_DOCUMENT_20','FLAG_DOCUMENT_21'], axis=1)
                            
    application_test.drop(['FLAG_DOCUMENT_2','FLAG_DOCUMENT_4','FLAG_DOCUMENT_7','FLAG_DOCUMENT_10','FLAG_DOCUMENT_12',
                            'FLAG_DOCUMENT_14','FLAG_DOCUMENT_15','FLAG_DOCUMENT_17','FLAG_DOCUMENT_19','FLAG_DOCUMENT_20','FLAG_DOCUMENT_21'], axis=1)
                            
    print("Highly correlated values dropped")
    
    application_train['CREDIT_GOODS_RATIO'] = application_train['AMT_CREDIT'] / application_train['AMT_GOODS_PRICE'] #oliviers version of aguiar features start
    application_test['CREDIT_GOODS_RATIO'] = application_test['AMT_CREDIT'] / application_test['AMT_GOODS_PRICE']
    
    application_train['NEW_CREDIT_TO_ANNUITY_RATIO'] = application_train['AMT_CREDIT'] / application_train['AMT_ANNUITY']
    application_test['NEW_CREDIT_TO_ANNUITY_RATIO'] = application_test['AMT_CREDIT'] / application_test['AMT_ANNUITY']
    
    application_train['NEW_INC_PER_CHLD'] = application_train['AMT_INCOME_TOTAL'] / (1 + application_train['CNT_CHILDREN'])
    application_test['NEW_INC_PER_CHLD'] = application_test['AMT_INCOME_TOTAL'] / (1 + application_test['CNT_CHILDREN'])
    
    inc_by_org = application_train[['AMT_INCOME_TOTAL', 'ORGANIZATION_TYPE']].groupby('ORGANIZATION_TYPE').median()['AMT_INCOME_TOTAL']
    inc_by_org1 = application_test[['AMT_INCOME_TOTAL', 'ORGANIZATION_TYPE']].groupby('ORGANIZATION_TYPE').median()['AMT_INCOME_TOTAL']
    
    application_train['NEW_INC_BY_ORG'] = application_train['ORGANIZATION_TYPE'].map(inc_by_org)
    application_test['NEW_INC_BY_ORG'] = application_test['ORGANIZATION_TYPE'].map(inc_by_org1)
    
    application_train['NEW_SOURCES_PROD'] = application_train['EXT_SOURCE_1'] * application_train['EXT_SOURCE_2'] * application_train['EXT_SOURCE_3']
    application_test['NEW_SOURCES_PROD'] = application_test['EXT_SOURCE_1'] * application_test['EXT_SOURCE_2'] * application_test['EXT_SOURCE_3']
    
    application_train['NEW_SCORES_STD'] = application_train[['EXT_SOURCE_1', 'EXT_SOURCE_2', 'EXT_SOURCE_3']].std(axis=1)
    application_train['NEW_SCORES_STD'] = application_train['NEW_SCORES_STD'].fillna(application_train['NEW_SCORES_STD'].mean())
    
    application_test['NEW_SCORES_STD'] = application_test[['EXT_SOURCE_1', 'EXT_SOURCE_2', 'EXT_SOURCE_3']].std(axis=1)
    application_test['NEW_SCORES_STD'] = application_test['NEW_SCORES_STD'].fillna(application_test['NEW_SCORES_STD'].mean())
    
    application_train['NEW_CAR_TO_BIRTH_RATIO'] = application_train['OWN_CAR_AGE'] / application_train['DAYS_BIRTH']
    application_train['NEW_CAR_TO_EMPLOY_RATIO'] = application_train['OWN_CAR_AGE'] / application_train['DAYS_EMPLOYED']
    application_train['NEW_PHONE_TO_BIRTH_RATIO'] = application_train['DAYS_LAST_PHONE_CHANGE'] / application_train['DAYS_BIRTH']
    application_train['NEW_PHONE_TO_EMPLOY_RATIO'] = application_train['DAYS_LAST_PHONE_CHANGE'] / application_train['DAYS_EMPLOYED']
    application_train['NEW_CREDIT_TO_INCOME_RATIO'] = application_train['AMT_CREDIT'] / application_train['AMT_INCOME_TOTAL']
    
    application_test['NEW_CAR_TO_BIRTH_RATIO'] = application_test['OWN_CAR_AGE'] / application_test['DAYS_BIRTH']
    application_test['NEW_CAR_TO_EMPLOY_RATIO'] = application_test['OWN_CAR_AGE'] / application_test['DAYS_EMPLOYED']
    application_test['NEW_PHONE_TO_BIRTH_RATIO'] = application_test['DAYS_LAST_PHONE_CHANGE'] / application_test['DAYS_BIRTH']
    application_test['NEW_PHONE_TO_EMPLOY_RATIO'] = application_test['DAYS_LAST_PHONE_CHANGE'] / application_test['DAYS_EMPLOYED']
    application_test['NEW_CREDIT_TO_INCOME_RATIO'] = application_test['AMT_CREDIT'] / application_test['AMT_INCOME_TOTAL']
    
    print("done processing boosting features")
    print("done with feature engg, merging tables now")
    
    
    application_train = application_train.merge(right=avg_buro.reset_index(), how='left', on='SK_ID_CURR')
    application_test = application_test.merge(right=avg_buro.reset_index(), how='left', on='SK_ID_CURR')
    
    application_train = application_train.merge(right=avg_prev.reset_index(), how='left', on='SK_ID_CURR')
    application_test = application_test.merge(right=avg_prev.reset_index(), how='left', on='SK_ID_CURR')
    
    application_train = application_train.merge(right=avg_pos.reset_index(), how='left', on='SK_ID_CURR')
    application_test = application_test.merge(right=avg_pos.reset_index(), how='left', on='SK_ID_CURR')
    
    application_train = application_train.merge(right=avg_cc_bal.reset_index(), how='left', on='SK_ID_CURR')
    application_test = application_test.merge(right=avg_cc_bal.reset_index(), how='left', on='SK_ID_CURR')
    
    application_train['cc_bal_INCOME_RATIO'] = application_train['cc_bal_AMT_BALANCE'] / application_train['AMT_INCOME_TOTAL']
    application_train['cc_payment_INCOME_RATIO'] = application_train['cc_bal_AMT_DRAWINGS_ATM_CURRENT'] / application_train['AMT_INCOME_TOTAL']
    
    application_test['cc_bal_INCOME_RATIO'] = application_test['cc_bal_AMT_BALANCE'] / application_test['AMT_INCOME_TOTAL']
    application_test['cc_payment_INCOME_RATIO'] = application_test['cc_bal_AMT_DRAWINGS_ATM_CURRENT'] / application_test['AMT_INCOME_TOTAL']
    
    application_train = application_train.merge(right=avg_inst.reset_index(), how='left', on='SK_ID_CURR')
    application_test = application_test.merge(right=avg_inst.reset_index(), how='left', on='SK_ID_CURR')
    
    print("done merging table, about to train model now")
    
    del avg_buro, avg_prev, avg_pos, avg_cc_bal, avg_inst
    gc.collect()
    
    print(application_train.shape)
    print(application_test.shape)
    return application_train, application_test, y
    
    

def train_model(data_, test_, y_, folds_):

    oof_preds = np.zeros(data_.shape[0])
    sub_preds = np.zeros(test_.shape[0])
    
    feature_importance_df = pd.DataFrame()
    
    feats = [f for f in data_.columns if f not in ['SK_ID_CURR']]
    
    for n_fold, (trn_idx, val_idx) in enumerate(folds_.split(data_)):
        trn_x, trn_y = data_[feats].iloc[trn_idx], y_.iloc[trn_idx]
        val_x, val_y = data_[feats].iloc[val_idx], y_.iloc[val_idx]
        
        clf = LGBMClassifier(
            n_estimators=10000,
            learning_rate=0.03,
            num_leaves=34,
            colsample_bytree=0.9497036,
            subsample=0.8715623,
            max_depth=8,
            reg_alpha=0.041545473,
            reg_lambda=0.0735294,
            min_split_gain=0.0222415,
            min_child_weight=39.3259775,
            silent=-1,
            verbose=-1,
        )
        
        clf.fit(trn_x, trn_y, 
                eval_set= [(trn_x, trn_y), (val_x, val_y)], 
                eval_metric='auc', verbose=100, early_stopping_rounds=100  #30
               )
        
        oof_preds[val_idx] = clf.predict_proba(val_x, num_iteration=clf.best_iteration_)[:, 1]
        sub_preds += clf.predict_proba(test_[feats], num_iteration=clf.best_iteration_)[:, 1] / folds_.n_splits
        
        fold_importance_df = pd.DataFrame()
        fold_importance_df["feature"] = feats
        fold_importance_df["importance"] = clf.feature_importances_
        fold_importance_df["fold"] = n_fold + 1
        feature_importance_df = pd.concat([feature_importance_df, fold_importance_df], axis=0)
        
        print('Fold %2d AUC : %.6f' % (n_fold + 1, roc_auc_score(val_y, oof_preds[val_idx])))
        del clf, trn_x, trn_y, val_x, val_y
        gc.collect()
        
    print('Full AUC score %.6f' % roc_auc_score(y_, oof_preds)) 
    
    test_['TARGET'] = sub_preds

    return oof_preds, test_[['SK_ID_CURR', 'TARGET']], feature_importance_df
    

"""def display_importances(feature_importance_df_):
    # Plot feature importances
    cols = feature_importance_df_[["feature", "importance"]].groupby("feature").mean().sort_values(
        by="importance", ascending=False)[:50].index
    
    best_features = feature_importance_df_.loc[feature_importance_df_.feature.isin(cols)]
    
    plt.figure(figsize=(8,10))
    sns.barplot(x="importance", y="feature", 
                data=best_features.sort_values(by="importance", ascending=False))
    plt.title('LightGBM Features (avg over folds)')
    plt.tight_layout()
    plt.savefig('lgbm_importances.png')


def display_roc_curve(y_, oof_preds_, folds_idx_):
    # Plot ROC curves
    plt.figure(figsize=(6,6))
    scores = [] 
    for n_fold, (_, val_idx) in enumerate(folds_idx_):  
        # Plot the roc curve
        fpr, tpr, thresholds = roc_curve(y_.iloc[val_idx], oof_preds_[val_idx])
        score = roc_auc_score(y_.iloc[val_idx], oof_preds_[val_idx])
        scores.append(score)
        plt.plot(fpr, tpr, lw=1, alpha=0.3, label='ROC fold %d (AUC = %0.4f)' % (n_fold + 1, score))
    
    plt.plot([0, 1], [0, 1], linestyle='--', lw=2, color='r', label='Luck', alpha=.8)
    fpr, tpr, thresholds = roc_curve(y_, oof_preds_)
    score = roc_auc_score(y_, oof_preds_)
    plt.plot(fpr, tpr, color='b',
             label='Avg ROC (AUC = %0.4f $\pm$ %0.4f)' % (score, np.std(scores)),
             lw=2, alpha=.8)
    
    plt.xlim([-0.05, 1.05])
    plt.ylim([-0.05, 1.05])
    plt.xlabel('False Positive Rate')
    plt.ylabel('True Positive Rate')
    plt.title('LightGBM ROC Curve')
    plt.legend(loc="lower right")
    plt.tight_layout()
    
    plt.savefig('roc_curve.png')


def display_precision_recall(y_, oof_preds_, folds_idx_):
    # Plot ROC curves
    plt.figure(figsize=(6,6))
    
    scores = [] 
    for n_fold, (_, val_idx) in enumerate(folds_idx_):  
        # Plot the roc curve
        fpr, tpr, thresholds = roc_curve(y_.iloc[val_idx], oof_preds_[val_idx])
        score = average_precision_score(y_.iloc[val_idx], oof_preds_[val_idx])
        scores.append(score)
        plt.plot(fpr, tpr, lw=1, alpha=0.3, label='AP fold %d (AUC = %0.4f)' % (n_fold + 1, score))
    
    precision, recall, thresholds = precision_recall_curve(y_, oof_preds_)
    score = average_precision_score(y_, oof_preds_)
    plt.plot(precision, recall, color='b',
             label='Avg ROC (AUC = %0.4f $\pm$ %0.4f)' % (score, np.std(scores)),
             lw=2, alpha=.8)
    
    plt.xlim([-0.05, 1.05])
    plt.ylim([-0.05, 1.05])
    plt.xlabel('Recall')
    plt.ylabel('Precision')
    plt.title('LightGBM Recall / Precision')
    plt.legend(loc="best")
    plt.tight_layout()
    
    plt.savefig('recall_precision_curve.png')"""

if __name__ == '__main__':
    gc.enable()
    # Build model inputs
    application_train, application_test, y = build_model_input()
    # Create Folds
    folds = KFold(n_splits=5, shuffle=True, random_state=546789)
    # Train model and get oof and test predictions
    oof_preds, test_preds, importances = train_model(application_train, application_test, y, folds)
    # Save test predictions
    test_preds.to_csv('first_submission_new.csv', index=False)
    # Display a few graphs
    """folds_idx = [(trn_idx, val_idx) for trn_idx, val_idx in folds.split(data)]
    display_importances(feature_importance_df_=importances)
    display_roc_curve(y_=y, oof_preds_=oof_preds, folds_idx_=folds_idx)
    display_precision_recall(y_=y, oof_preds_=oof_preds, folds_idx_=folds_idx)"""
    