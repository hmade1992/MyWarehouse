import streamlit as st
import pandas as pd
import os
import plotly.express as px # للمخططات الجمالية
from io import BytesIO
from datetime import datetime
import pdfplumber
import re
from difflib import SequenceMatcher

# 1. إعدادات الصفحة والشكل العام
st.set_page_config(
    page_title="نظام إدارة مخازن النواقية",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded"
)

# تصميم جمالي مخصص (CSS)
st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .stMetric { background-color: #1e2130; padding: 15px; border-radius: 10px; border: 1px solid #31333f; }
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; background-color: #ff4b4b; color: white; }
    .sidebar .sidebar-content { background-image: linear-gradient(#2e7bcf,#2e7bcf); color: white; }
    </style>
    """, unsafe_allow_html=True)

# 2. إنشاء ملفات البيانات إذا لم توجد
if not os.path.exists('inventory.csv'):
    pd.DataFrame(columns=['الصنف', 'الافتتاحي', 'المتبقي']).to_csv('inventory.csv', index=False)
if not os.path.exists('sales.csv'):
    pd.DataFrame(columns=['التاريخ', 'الصنف', 'أمتار', 'ملاحظة']).to_csv('sales.csv', index=False)

# 3. تحميل البيانات
inv_df = pd.read_csv('inventory.csv')
sales_df = pd.read_csv('sales.csv')

# إضافة عمود الملاحظة إذا لم يكن موجوداً (للملفات القديمة)
if 'ملاحظة' not in sales_df.columns:
    sales_df['ملاحظة'] = ''

# 3.1 دوال مساعدة لمعالجة فواتير PDF
def extract_text_from_pdf(pdf_file):
    """استخراج النص من ملف PDF"""
    try:
        text = ""
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        return text
    except Exception as e:
        st.error(f"خطأ في قراءة ملف PDF: {e}")
        return None

def extract_table_from_pdf(pdf_file):
    """استخراج الجداول من ملف PDF"""
    try:
        all_tables = []
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                if tables:
                    all_tables.extend(tables)
        return all_tables
    except Exception as e:
        st.error(f"خطأ في استخراج الجداول: {e}")
        return []

def find_product_column_index(headers):
    """العثور على فهرس عمود المنتج"""
    keywords = ['المنتج', 'الصنف', 'الاسم', 'product', 'item', 'name']
    for i, header in enumerate(headers):
        if header:
            header_text = str(header).strip().lower()
            for keyword in keywords:
                if keyword in header_text:
                    return i
    return None

def find_quantity_column_index(headers):
    """العثور على فهرس عمود الكمية"""
    keywords = ['الكمية', 'quantity', 'qty', 'عدد', 'amount']
    for i, header in enumerate(headers):
        if header:
            header_text = str(header).strip().lower()
            for keyword in keywords:
                if keyword in header_text:
                    return i
    return None

def extract_products_from_text(text):
    """استخراج المنتجات من النص باستخدام regex"""
    products = []
    lines = text.split('\n')
    
    product_pattern = r'(.+?)\s+(\d+\.?\d*)'
    in_table = False
    
    for i, line in enumerate(lines):
        line_lower = line.strip().lower()
        # البحث عن بداية الجدول
        if 'المنتج' in line_lower and 'الكمية' in line_lower:
            in_table = True
            continue
        
        if in_table and line.strip():
            # محاولة استخراج المنتج والكمية
            match = re.search(product_pattern, line)
            if match:
                product_name = match.group(1).strip()
                quantity = match.group(2).strip()
                try:
                    qty = float(quantity)
                    if qty > 0:
                        products.append({'product': product_name, 'quantity': qty})
                except:
                    pass
    
    return products

def extract_products_from_tables(tables):
    """استخراج المنتجات من الجداول"""
    products = []
    
    for table in tables:
        if not table or len(table) < 2:
            continue
        
        # البحث عن صف الرؤوس
        headers = table[0] if table else []
        product_col = find_product_column_index(headers)
        quantity_col = find_quantity_column_index(headers)
        
        if product_col is not None and quantity_col is not None:
            # استخراج البيانات من الصفوف
            for row in table[1:]:
                if len(row) > max(product_col, quantity_col):
                    product_name = str(row[product_col]).strip() if row[product_col] else ""
                    quantity_str = str(row[quantity_col]).strip() if row[quantity_col] else "0"
                    
                    if product_name and product_name.lower() not in ['المنتج', 'الصنف', 'الاسم', '']:
                        try:
                            qty = float(re.sub(r'[^\d.]', '', quantity_str))
                            if qty > 0:
                                products.append({'product': product_name, 'quantity': qty})
                        except:
                            pass
    
    return products

def similarity_score(str1, str2):
    """حساب درجة التشابه بين نصين"""
    return SequenceMatcher(None, str1.lower().strip(), str2.lower().strip()).ratio()

def match_product_with_inventory(product_name, inventory_df, threshold=0.6):
    """مطابقة المنتج مع المخزن"""
    best_match = None
    best_score = 0
    
    for idx, inv_product in enumerate(inventory_df['الصنف']):
        score = similarity_score(str(product_name), str(inv_product))
        if score > best_score:
            best_score = score
            best_match = (idx, inv_product)
    
    if best_score >= threshold:
        return best_match[1], best_score
    return None, best_score

def process_pdf_invoices(pdf_files, inventory_df):
    """معالجة ملفات PDF متعددة"""
    all_extracted_products = []
    
    for pdf_file in pdf_files:
        # محاولة استخراج الجداول أولاً
        tables = extract_table_from_pdf(pdf_file)
        products = extract_products_from_tables(tables)
        
        # إذا لم نجد منتجات في الجداول، جرب استخراج النص
        if not products:
            text = extract_text_from_pdf(pdf_file)
            if text:
                products = extract_products_from_text(text)
        
        # مطابقة المنتجات مع المخزن
        matched_products = []
        for prod in products:
            matched_product, score = match_product_with_inventory(prod['product'], inventory_df)
            matched_products.append({
                'original_name': prod['product'],
                'matched_name': matched_product,
                'quantity': prod['quantity'],
                'match_score': score,
                'file_name': pdf_file.name
            })
        
        all_extracted_products.extend(matched_products)
    
    return all_extracted_products

# 4. القائمة الجانبية (شعار الشركة والتحكم)
with st.sidebar:
    # هنا يمكنك وضع رابط شعار الشركة
    # st.image("logo.png", width=200) # إذا كان عندك ملف الشعار باسم logo.png
    st.markdown("<h2 style='text-align: center; color: #ff4b4b;'>شعار الشركة</h2>", unsafe_allow_html=True)
    st.divider()
    
    page = st.radio("انتقل إلى:", ["🏠 لوحة التحكم والجرد", "📊 صفحة الإحصائيات المتقدمة", "📄 قراءة فواتير PDF", "⚙️ الإعدادات والرفع"])
    
    st.divider()
    st.info("مخزن النواقية الرئيسي")

# --- الصفحة الأولى: لوحة التحكم والجرد ---
if page == "🏠 لوحة التحكم والجرد":
    st.header("📦 إدارة السحب اليومي")
    
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        if not inv_df.empty:
            item = st.selectbox("اختر الصنف من المخزن:", inv_df['الصنف'].unique())
        else:
            st.warning("يرجى رفع ملف الأصناف أولاً")
            item = None
            
    with col2:
        m_sold = st.number_input("الكمية المباعة (أمتار):", min_value=0.0, step=0.1)
        
    with col3:
        st.write("##")
        if st.button("تسجيل السحب") and item:
            idx = inv_df[inv_df['الصنف'] == item].index[0]
            if inv_df.at[idx, 'المتبقي'] >= m_sold:
                inv_df.at[idx, 'المتبقي'] -= m_sold
                inv_df.to_csv('inventory.csv', index=False)
                # تسجيل الحركة
                new_row = pd.DataFrame([{'التاريخ': pd.Timestamp.now(), 'الصنف': item, 'أمتار': m_sold, 'ملاحظة': ''}])
                pd.concat([sales_df, new_row]).to_csv('sales.csv', index=False)
                st.success(f"تم خصم {m_sold} متر بنجاح")
                st.rerun()
            else:
                st.error("الكمية لا تكفي!")

    st.divider()
    
    # قسم تقسيم الجرد اليومي حسب الأيام
    st.subheader("📅 تقسيم الجرد اليومي حسب أيام الأسبوع")
    
    col_filter1, col_filter2 = st.columns([2, 1])
    
    with col_filter1:
        # أسماء الأيام بالعربية
        days_options = {
            'الكل': None,
            'السبت': 'Saturday',
            'الأحد': 'Sunday',
            'الإثنين': 'Monday',
            'الثلاثاء': 'Tuesday',
            'الأربعاء': 'Wednesday',
            'الخميس': 'Thursday',
            'الجمعة': 'Friday'
        }
        selected_day_ar = st.selectbox("اختر يوم الأسبوع:", list(days_options.keys()))
        selected_day_en = days_options[selected_day_ar]
    
    with col_filter2:
        st.write("##")
        # إنشاء ملف Excel للتحميل
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            inv_df.to_excel(writer, index=False, sheet_name='الجرد')
            # إضافة ورقة بالمبيعات إذا كانت موجودة
            if not sales_df.empty:
                sales_df.to_excel(writer, index=False, sheet_name='المبيعات')
        output.seek(0)
        
        # اسم الملف مع التاريخ
        today = datetime.now().strftime("%Y-%m-%d")
        filename = f"جرد_المخزن_{today}.xlsx"
        
        st.download_button(
            label="📥 تحميل ملف الجرد الكامل",
            data=output.getvalue(),
            file_name=filename,
            mime="application/vnd.openpyxl-officedocument.spreadsheetml.sheet"
        )
    
    # عرض البيانات حسب اليوم المحدد
    if selected_day_en and not sales_df.empty:
        # إنشاء نسخة من البيانات لتجنب تعديل الأصلية
        sales_df_filtered = sales_df.copy()
        # تحويل عمود التاريخ إلى datetime
        sales_df_filtered['التاريخ'] = pd.to_datetime(sales_df_filtered['التاريخ'])
        
        # تصفية البيانات حسب اليوم المحدد
        filtered_sales = sales_df_filtered[sales_df_filtered['التاريخ'].dt.day_name() == selected_day_en].copy()
        
        if not filtered_sales.empty:
            st.info(f"📊 بيانات يوم {selected_day_ar}")
            
            # إحصائيات اليوم
            col_stat1, col_stat2, col_stat3 = st.columns(3)
            with col_stat1:
                st.metric("عدد المعاملات", len(filtered_sales))
            with col_stat2:
                st.metric("إجمالي الأمتار المباعة", f"{filtered_sales['أمتار'].sum():,.1f} م")
            with col_stat3:
                st.metric("عدد الأصناف", filtered_sales['الصنف'].nunique())
            
            # جدول المبيعات لليوم المحدد
            st.subheader(f"📋 تفاصيل مبيعات {selected_day_ar}")
            st.dataframe(filtered_sales[['التاريخ', 'الصنف', 'أمتار']], use_container_width=True)
            
            # زر تحميل بيانات اليوم
            output_day = BytesIO()
            filtered_sales[['التاريخ', 'الصنف', 'أمتار']].to_excel(output_day, index=False, engine='openpyxl')
            output_day.seek(0)
            filename_day = f"مبيعات_{selected_day_ar}_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
            st.download_button(
                label=f"📥 تحميل بيانات {selected_day_ar}",
                data=output_day.getvalue(),
                file_name=filename_day,
                mime="application/vnd.openpyxl-officedocument.spreadsheetml.sheet"
            )
        else:
            st.warning(f"لا توجد بيانات مبيعات ليوم {selected_day_ar}")
    else:
        if not sales_df.empty and selected_day_ar == 'الكل':
            st.info("📊 عرض جميع بيانات المبيعات")
            st.dataframe(sales_df, use_container_width=True)
    
    st.divider()
    st.subheader("📋 حالة الجرد الحالي")
    st.dataframe(inv_df, use_container_width=True)

# --- الصفحة الثانية: الإحصائيات المتقدمة ---
elif page == "📊 صفحة الإحصائيات المتقدمة":
    st.header("📊 تحليل مبيعات الأمتار")
    
    if not sales_df.empty:
        # مؤشرات سريعة
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("إجمالي الأمتار المباعة", f"{sales_df['أمتار'].sum():,.1f} م")
        with c2:
            st.metric("أكثر صنف مبيعاً", sales_df.groupby('الصنف')['أمتار'].sum().idxmax())
        with c3:
            st.metric("عدد عمليات اليوم", len(sales_df[pd.to_datetime(sales_df['التاريخ']).dt.date == pd.Timestamp.now().date()]))

        # مخطط بياني جمالي للأصناف الأكثر مبيعاً
        st.subheader("🔝 الأصناف الأكثر طلباً (حسب الأمتار)")
        best_sellers = sales_df.groupby('الصنف')['أمتار'].sum().reset_index().sort_values(by='أمتار', ascending=False)
        fig = px.bar(best_sellers.head(10), x='الصنف', y='أمتار', color='أمتار', color_continuous_scale='Reds', template="plotly_dark")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("لا توجد بيانات مبيعات لعرض الإحصائيات بعد.")

# --- الصفحة الثالثة: قراءة فواتير PDF ---
elif page == "📄 قراءة فواتير PDF":
    st.header("📄 قراءة فواتير PDF - شركة مجال")
    
    st.info("💡 يمكنك رفع ملف أو أكثر من ملف PDF لقراءة الفواتير واستخراج المنتجات تلقائياً")
    
    # رفع ملفات PDF
    uploaded_pdfs = st.file_uploader(
        "اختر ملف/ملفات PDF للفواتير", 
        type=['pdf'], 
        accept_multiple_files=True
    )
    
    if uploaded_pdfs:
        st.success(f"تم رفع {len(uploaded_pdfs)} ملف PDF")
        
        # معالجة الملفات
        if st.button("🔍 استخراج البيانات من الفواتير", type="primary"):
            with st.spinner("جاري معالجة الفواتير..."):
                extracted_data = process_pdf_invoices(uploaded_pdfs, inv_df)
                
                if extracted_data:
                    # حفظ البيانات في session state
                    st.session_state['extracted_invoice_data'] = extracted_data
                    st.success(f"تم استخراج {len(extracted_data)} منتج من الفواتير")
                else:
                    st.warning("لم يتم العثور على منتجات في الفواتير المرفوعة")
                    st.session_state['extracted_invoice_data'] = []
    
    # عرض البيانات المستخرجة
    if 'extracted_invoice_data' in st.session_state and st.session_state['extracted_invoice_data']:
        st.divider()
        st.subheader("📋 المنتجات المستخرجة من الفواتير")
        
        extracted_data = st.session_state['extracted_invoice_data']
        
        # إنشاء DataFrame للعرض
        display_data = []
        for item in extracted_data:
            status = "✅ متطابق" if item['matched_name'] else "❌ غير متطابق"
            match_percentage = f"{item['match_score']*100:.1f}%"
            display_data.append({
                'اسم المنتج في الفاتورة': item['original_name'],
                'اسم المنتج المطابق': item['matched_name'] if item['matched_name'] else 'غير موجود',
                'الكمية': item['quantity'],
                'نسبة التطابق': match_percentage,
                'الحالة': status,
                'اسم الملف': item['file_name']
            })
        
        df_display = pd.DataFrame(display_data)
        st.dataframe(df_display, use_container_width=True)
        
        # تصفية المنتجات المتطابقة فقط
        matched_items = [item for item in extracted_data if item['matched_name']]
        
        if matched_items:
            st.divider()
            st.subheader("✅ المنتجات الجاهزة للخصم من المخزن")
            
            # جدول للمنتجات المتطابقة
            matched_display = []
            for item in matched_items:
                # التحقق من الكمية المتوفرة
                inv_idx = inv_df[inv_df['الصنف'] == item['matched_name']].index
                available_qty = inv_df.at[inv_idx[0], 'المتبقي'] if len(inv_idx) > 0 else 0
                can_deduct = available_qty >= item['quantity']
                
                matched_display.append({
                    'الصنف': item['matched_name'],
                    'الكمية المطلوبة': item['quantity'],
                    'المتاح في المخزن': available_qty,
                    'الحالة': '✅ متوفر' if can_deduct else '❌ غير كافي'
                })
            
            df_matched = pd.DataFrame(matched_display)
            st.dataframe(df_matched, use_container_width=True)
            
            # زر التأكيد
            st.divider()
            col_confirm1, col_confirm2 = st.columns([3, 1])
            
            with col_confirm1:
                st.info(f"سيتم خصم {len(matched_items)} منتج من المخزن")
            
            with col_confirm2:
                if st.button("✅ تأكيد الخصم من المخزن", type="primary", use_container_width=True):
                    # تحديث المخزن والمبيعات
                    success_count = 0
                    error_count = 0
                    
                    for item in matched_items:
                        try:
                            inv_idx = inv_df[inv_df['الصنف'] == item['matched_name']].index[0]
                            
                            if inv_df.at[inv_idx, 'المتبقي'] >= item['quantity']:
                                # خصم من المخزن
                                inv_df.at[inv_idx, 'المتبقي'] -= item['quantity']
                                success_count += 1
                                
                                # تسجيل في المبيعات
                                new_sale = pd.DataFrame([{
                                    'التاريخ': pd.Timestamp.now(),
                                    'الصنف': item['matched_name'],
                                    'أمتار': item['quantity'],
                                    'ملاحظة': 'تم الإدخال عبر فاتورة PDF'
                                }])
                                sales_df = pd.concat([sales_df, new_sale], ignore_index=True)
                            else:
                                error_count += 1
                        except Exception as e:
                            error_count += 1
                            st.error(f"خطأ في معالجة {item['matched_name']}: {e}")
                    
                    # حفظ التغييرات
                    if success_count > 0:
                        inv_df.to_csv('inventory.csv', index=False)
                        sales_df.to_csv('sales.csv', index=False)
                        st.success(f"✅ تم خصم {success_count} منتج بنجاح!")
                        if error_count > 0:
                            st.warning(f"⚠️ {error_count} منتج لم يتم خصمه (كمية غير كافية)")
                        
                        # مسح البيانات المستخرجة
                        st.session_state['extracted_invoice_data'] = []
                        st.rerun()
                    else:
                        st.error("لم يتم خصم أي منتج. يرجى التحقق من الكميات المتاحة.")
        else:
            st.warning("⚠️ لا توجد منتجات متطابقة مع المخزن. يرجى التحقق من أسماء المنتجات في الفواتير.")

# --- الصفحة الرابعة: الإعدادات والرفع ---
elif page == "⚙️ الإعدادات والرفع":
    st.header("⚙️ إدارة البيانات")
    
    st.subheader("📥 رفع ملف إكسل النواقية الرئيسي")
    uploaded_file = st.file_uploader("اختر ملف Excel يحتوي على (الصنف، الكمية)", type=['xlsx'])
    
    if uploaded_file:
        try:
            raw_data = pd.read_excel(uploaded_file)
            raw_data.columns = ['الصنف', 'الافتتاحي']
            raw_data['المتبقي'] = raw_data['الافتتاحي']
            raw_data.to_csv('inventory.csv', index=False)
            st.success("✅ تم تحديث المخزن الرئيسي بنجاح!")
        except Exception as e:
            st.error(f"خطأ في شكل الملف: {e}")

    st.divider()
    if st.button("⚠️ مسح جميع البيانات وابدأ من جديد"):
        if os.path.exists('inventory.csv'): os.remove('inventory.csv')
        if os.path.exists('sales.csv'): os.remove('sales.csv')
        st.rerun()