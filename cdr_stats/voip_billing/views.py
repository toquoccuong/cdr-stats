#
# CDR-Stats License
# http://www.cdr-stats.org
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (C) 2011-2013 Star2Billing S.L.
#
# The Initial Developer of the Original Code is
# Arezqui Belaid <info@star2billing.com>
#
from django.contrib.auth.decorators import login_required, permission_required
from django.views.decorators.cache import cache_page
from django.http import HttpResponse
from django.shortcuts import render_to_response
from django.conf import settings
from django.utils.translation import gettext as _
from django.template.context import RequestContext
from voip_billing.models import VoIPRetailRate
from voip_billing.forms import PrefixRetailRrateForm, SimulatorForm, DailyBillingForm,\
    HourlyBillingForm
from voip_billing.function_def import prefix_allowed_to_call
from voip_billing.rate_engine import rate_engine
from voip_billing.constants import RATE_COLUMN_NAME
from user_profile.models import UserProfile
from cdr.views import check_user_accountcode, check_cdr_exists, check_user_voipplan
from cdr.functions_def import chk_account_code
from cdr.aggregate import pipeline_daily_billing_report, pipeline_hourly_billing_report
from common.common_functions import current_view, ceil_strdate, get_pagination_vars
from datetime import datetime
import logging
import time
import requests
import ast
import csv


@permission_required('user_profile.call_rate', login_url='/')
@login_required
@check_user_voipplan
#@cache_page(60 * 5)
def voip_rates(request):
    """List voip call rates according to country prefix

    **Attributes**:

        * ``template`` - voip_billing/rates.html
        * ``form`` - PrefixRetailRrateForm

    **Logic Description**:

        get all call rates from voip rate API and list them in template
        with pagination & sorting column
    """
    template = 'voip_billing/rates.html'
    form = PrefixRetailRrateForm()
    final_rate_list = []
    # Get pagination data
    sort_col_field_list = ['prefix', 'retail_rate', 'destination']
    default_sort_field = 'prefix'
    pagination_data =\
        get_pagination_vars(request, sort_col_field_list, default_sort_field)

    PAGE_SIZE = pagination_data['PAGE_SIZE']
    sort_order = pagination_data['sort_order']    
    
    order = 'ASC'
    if "-" in sort_order:
        order = 'DESC'
        sort_order = sort_order[1:]

    dialcode = ''
    if request.method == 'POST':
        form = PrefixRetailRrateForm(request.POST)
        if form.is_valid():
            dialcode = request.POST.get('prefix')
            request.session['dialcode'] = dialcode
    else:
        # pagination with prefix code
        if (request.session.get('dialcode') and
           (request.GET.get('page') or request.GET.get('sort_by'))):
            dialcode = request.session.get('dialcode')
            form = PrefixRetailRrateForm(initial={'prefix': dialcode})
        else:
            # Reset variables
            request.session['dialcode'] = ''
            request.session['final_rate_list'] = ''
            dialcode = ''

    if dialcode:
        response = requests.get('http://localhost:8000/api/v1/voip_rate/?dialcode=%s&sort_field=%s&sort_order=%s' % (dialcode, sort_order, order),
            auth=(request.user, request.user))
    else:
        # Default listing or rate
        response = requests.get('http://localhost:8000/api/v1/voip_rate/?sort_field=%s&sort_order=%s' % (sort_order, order),
            auth=(request.user, request.user))

    rate_list = response.content
    # due to string response of API, we need to convert response in to array
    rate_list = rate_list.replace('[', '').replace(']', '').replace('}, {', '}|{').split('|')
    for i in rate_list:
        # convert string into dict
        final_rate_list.append(ast.literal_eval(i))
        
    request.session['final_rate_list'] = final_rate_list

    variables = RequestContext(request, {
        'module': current_view(request),
        'form': form,
        'user': request.user,
        'rate_list': final_rate_list,
        'rate_list_count': len(final_rate_list),
        'col_name_with_order': pagination_data['col_name_with_order'],
        'PAGE_SIZE': PAGE_SIZE,
        'RATE_COLUMN_NAME': RATE_COLUMN_NAME,
        'sort_order': sort_order,
    })
    return render_to_response(template, variables,
           context_instance=RequestContext(request))


@permission_required('user_profile.export_call_rate', login_url='/')
@login_required
def export_rate(request):
    """
    **Logic Description**:

        get the prifix rates  from voip rate API
        according to search parameters & store into csv file
    """
    # get the response object, this can be used as a stream
    response = HttpResponse(mimetype='text/csv')
    # force download
    response['Content-Disposition'] = 'attachment;filename=call_rate.csv'
    # the csv writer

    writer = csv.writer(response, dialect=csv.excel_tab)
    writer.writerow(['prefix', 'destination', 'retail_rate'])
    final_result = []
    if request.session.get('final_rate_list'):
        final_result = request.session['final_rate_list']

    for row in final_result:
        writer.writerow([
            row['prefix'],
            row['prefix__destination'],
            row['retail_rate'],
        ])
    return response


@permission_required('user_profile.simulator', login_url='/')
@check_user_voipplan
@login_required
def simulator(request):
    """Client Simulator
    To view rate according to VoIP Plan & Destination No.

    **Attributes**:

        * ``template`` - voip_billing/simulator.html
        * ``form`` - SimulatorForm

    **Logic Description**:

        get min call rates for destination from rate_engine and display them in template
    """
    template = 'voip_billing/simulator.html'
    data = []
    form = SimulatorForm(request.user)
    # Get Voip Plan ID according to USER
    voipplan_id = UserProfile.objects.get(user=request.user).voipplan_id

    if request.method == 'POST':
        form = SimulatorForm(request.user, request.POST)
        if form.is_valid():
            # IS recipient_phone_no/destination no is valid prefix
            # (Not banned Prefix) ?
            destination_no = request.POST.get("destination_no")
            allowed = prefix_allowed_to_call(destination_no, voipplan_id)
            if allowed:
                query = rate_engine(destination_no=destination_no, voipplan_id=voipplan_id)
                for i in query:
                    r_r_plan = VoIPRetailRate.objects.get(id=i.rrid)
                    data.append((voipplan_id,
                                 r_r_plan.voip_retail_plan_id.name,
                                 i.retail_rate))
    data = {
        'module': current_view(request),
        'form': form,
        'data': data,
    }
    return render_to_response(template, data,
        context_instance=RequestContext(request))


@permission_required('user_profile.daily_billing', login_url='/')
@check_cdr_exists
@check_user_accountcode
@check_user_voipplan
@login_required
def daily_billing_report(request):
    """CDR billing graph by daily basis

    **Attributes**:

        * ``template`` - voip_billing/daily_billing_report.html
        * ``form`` - DailyBillingForm
        * ``mongodb_data_set`` - MONGO_CDRSTATS['DAILY_ANALYTIC']
        * ``aggregate`` - pipeline_daily_billing_report()

    **Logic Description**:

        get all call records from mongodb collection for
        daily billing analytics for given date
    """
    template = 'voip_billing/daily_billing_report.html'
    search_tag = 0
    switch_id = 0
    start_date = ''
    end_date = ''
    tday = datetime.today()
    # assign initial value in form fields
    form = DailyBillingForm(initial={'from_date': tday.strftime('%Y-%m-%d'),
                                     'to_date': tday.strftime('%Y-%m-%d')})
    if request.method == 'POST':
        search_tag = 1
        form = DailyBillingForm(request.POST)
        if "from_date" in request.POST:
            from_date = request.POST['from_date']
            start_date = ceil_strdate(from_date, 'start')

        if "to_date" in request.POST:
            to_date = request.POST['to_date']
            end_date = ceil_strdate(to_date, 'end')

        if "switch" in request.POST:
            switch_id = request.POST['switch']
    else:
        start_date = datetime(tday.year, tday.month, tday.day, 0, 0, 0, 0)
        end_date = datetime(tday.year, tday.month, tday.day, 23, 59, 59, 999999)

    query_var = {}
    if switch_id and int(switch_id) != 0:
        query_var['metadata.switch_id'] = int(switch_id)

    query_var['metadata.date'] = {'$gte': start_date, '$lt': end_date}
    if not request.user.is_superuser:  # not superuser
        query_var['metadata.accountcode'] = chk_account_code(request)

    logging.debug('Aggregate daily billing analytic')
    pipeline = pipeline_daily_billing_report(query_var)

    logging.debug('Before Aggregate')
    list_data = settings.DBCON.command('aggregate',
                                       settings.MONGO_CDRSTATS['DAILY_ANALYTIC'],
                                       pipeline=pipeline)

    logging.debug('After Aggregate')
    daily_data = dict()
    total_data = []
    if list_data:
        for doc in list_data['result']:
            # Get date from aggregate result array
            graph_day = datetime(int(doc['_id'][0:4]),
                                 int(doc['_id'][4:6]),
                                 int(doc['_id'][6:8]),
                                 0, 0, 0, 0)
            # convert date into timestamp value
            dt = int(1000 * time.mktime(graph_day.timetuple()))

            # if timestamp value in daily_data, then update dict value
            if dt in daily_data:
                daily_data[dt]['buy_cost_per_day'] += float(doc['buy_cost_per_day'])
                daily_data[dt]['sell_cost_per_day'] += float(doc['sell_cost_per_day'])
            else:
                # assign new timestamp value in daily_data with dict value
                daily_data[dt] = {
                    'buy_cost_per_day': float(doc['buy_cost_per_day']),
                    'sell_cost_per_day': float(doc['sell_cost_per_day']),
                }

            # apply sorting on timestamp value
            total_data = daily_data.items()
            total_data = sorted(total_data, key=lambda k: k[0])

    data = {
        'module': current_view(request),
        'form': form,
        'search_tag': search_tag,
        'total_data': total_data,
        'start_date': start_date,
        'end_date': end_date,
    }
    return render_to_response(template, data, context_instance=RequestContext(request))


@permission_required('user_profile.hourly_billing', login_url='/')
@check_cdr_exists
@check_user_accountcode
@check_user_voipplan
@login_required
def hourly_billing_report(request):
    """CDR billing graph by hourly basis

    **Attributes**:

        * ``template`` - voip_billing/hourly_billing_report.html
        * ``form`` - HourlyBillingForm
        * ``mongodb_data_set`` - MONGO_CDRSTATS['DAILY_ANALYTIC']
        * ``aggregate`` - pipeline_hourly_billing_report()

    **Logic Description**:

        get all call records from mongodb collection for
        hourly billing analytics for given date
    """
    template = 'voip_billing/hourly_billing_report.html'
    search_tag = 0
    switch_id = 0
    start_date = ''
    end_date = ''
    tday = datetime.today()
    # assign initial value in form fields
    form = HourlyBillingForm(initial={'from_date': tday.strftime('%Y-%m-%d')})
    if request.method == 'POST':
        search_tag = 1
        form = HourlyBillingForm(request.POST)
        if "from_date" in request.POST:
            from_date = request.POST['from_date']
            start_date = ceil_strdate(from_date, 'start')

            start_date = datetime(start_date.year, start_date.month,
                start_date.day, 0, 0, 0, 0)
            end_date = datetime(start_date.year, start_date.month,
                start_date.day, 23, 59, 59, 999999)

        if "switch" in request.POST:
            switch_id = request.POST['switch']
    else:
        start_date = datetime(tday.year, tday.month, tday.day, 0, 0, 0, 0)
        end_date = datetime(tday.year, tday.month, tday.day, 23, 59, 59, 999999)

    query_var = {}
    if switch_id and int(switch_id) != 0:
        query_var['metadata.switch_id'] = int(switch_id)

    query_var['metadata.date'] = {'$gte': start_date, '$lt': end_date}
    if not request.user.is_superuser:  # not superuser
        query_var['metadata.accountcode'] = chk_account_code(request)

    logging.debug('Aggregate hourly billing analytic')
    pipeline = pipeline_hourly_billing_report(query_var)

    logging.debug('Before Aggregate')
    list_data = settings.DBCON.command('aggregate',
                                       settings.MONGO_CDRSTATS['DAILY_ANALYTIC'],
                                       pipeline=pipeline)

    logging.debug('After Aggregate')

    total_buy_record = {}
    total_sell_record = {}
    if list_data:
        for doc in list_data['result']:
            # Get called_time from aggregate result array
            called_time = datetime(int(doc['_id'][0:4]),
                                   int(doc['_id'][4:6]),
                                   int(doc['_id'][6:8]))

            buy_hours = {}
            sell_hours = {}
            # Assign 0 - 23 hrs in dict variable and initialize them with 0
            for hr in range(0, 24):
                buy_hours[hr] = 0
                sell_hours[hr] = 0

            # update dict variables with aggregate data
            for dict_in_list in doc['buy_cost_per_hour']:
                for key, value in dict_in_list.iteritems():
                    buy_hours[int(key)] += float(value)

            for dict_in_list in doc['sell_cost_per_hour']:
                for key, value in dict_in_list.iteritems():
                    sell_hours[int(key)] += float(value)

            # Assign buy_hours/sell_hours variables to another
            # total_buy_record/total_sell_record variables which will
            # store per day data
            total_buy_record[str(called_time)[:10]] = buy_hours
            total_sell_record[str(called_time)[:10]] = sell_hours

    data = {
        'module': current_view(request),
        'form': form,
        'search_tag': search_tag,
        'total_buy_record': total_buy_record,
        'total_sell_record': total_sell_record,
        'start_date': start_date,
    }
    return render_to_response(template, data, context_instance=RequestContext(request))
