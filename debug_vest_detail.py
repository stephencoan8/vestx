"""Debug script to test get_complete_data method"""
import sys
import traceback
from app import create_app
from app.models.vest_event import VestEvent
from app.models.user import User
from flask_login import login_user

app = create_app()

with app.app_context():
    # Get first user
    user = User.query.first()
    if not user:
        print("No users found in database")
        sys.exit(1)
    
    print(f"Testing with user: {user.id} - {user.email}")
    
    # Get first vest event
    vest = VestEvent.query.first()
    if not vest:
        print("No vest events found in database")
        sys.exit(1)
    
    print(f"Testing with vest: {vest.id} - Date: {vest.vest_date}")
    print(f"Grant: {vest.grant_id if vest.grant else 'NO GRANT'}")
    
    if vest.grant:
        print(f"Grant type: {vest.grant.grant_type}")
        print(f"Share type: {vest.grant.share_type}")
        print(f"Grant user: {vest.grant.user_id}")
    
    # Try to get user key
    print("\n=== Getting user key ===")
    try:
        user_key = user.get_decrypted_user_key()
        print(f"User key obtained: {len(user_key) if user_key else 0} bytes")
    except Exception as e:
        print(f"ERROR getting user key: {e}")
        traceback.print_exc()
        user_key = b''
    
    # Try to call get_complete_data
    print("\n=== Calling get_complete_data ===")
    try:
        vest_data = vest.get_complete_data(
            user_key=user_key,
            current_price=None,
            tax_profile=None,
            annual_incomes=None,
            sales_data=None,
            exercises_data=None
        )
        print(f"SUCCESS! Got vest_data with keys: {list(vest_data.keys())}")
        
        # Print key values
        print(f"\nKey values:")
        print(f"  vest_id: {vest_data.get('vest_id')}")
        print(f"  has_vested: {vest_data.get('has_vested')}")
        print(f"  is_iso: {vest_data.get('is_iso')}")
        print(f"  shares_vested: {vest_data.get('shares_vested')}")
        print(f"  price_at_vest: {vest_data.get('price_at_vest')}")
        print(f"  current_price: {vest_data.get('current_price')}")
        print(f"  error: {vest_data.get('error')}")
        
    except Exception as e:
        print(f"ERROR calling get_complete_data: {e}")
        traceback.print_exc()
    
    print("\n=== Testing route logic ===")
    # Simulate what the route does
    try:
        from app.models.tax_rate import UserTaxProfile
        from app.models.annual_income import AnnualIncome
        from app.models.stock_sale import StockSale
        from app.models.iso_exercise import ISOExercise
        
        tax_profile = UserTaxProfile.query.filter_by(user_id=user.id).first()
        print(f"Tax profile: {tax_profile.id if tax_profile else 'None'}")
        
        annual_incomes = AnnualIncome.query.filter_by(user_id=user.id).all()
        annual_incomes_dict = {ai.year: ai.annual_income for ai in annual_incomes}
        print(f"Annual incomes: {len(annual_incomes)} entries")
        
        sales = StockSale.query.filter_by(vest_event_id=vest.id).all()
        print(f"Sales: {len(sales)}")
        
        exercises = ISOExercise.query.filter_by(vest_event_id=vest.id).all()
        print(f"Exercises: {len(exercises)}")
        
        vest_data = vest.get_complete_data(
            user_key=user_key,
            current_price=None,
            tax_profile=tax_profile,
            annual_incomes=annual_incomes_dict,
            sales_data=sales,
            exercises_data=exercises
        )
        print(f"SUCCESS with full params! Keys: {list(vest_data.keys())}")
        if 'error' in vest_data:
            print(f"ERROR in vest_data: {vest_data['error']}")
        
    except Exception as e:
        print(f"ERROR in route simulation: {e}")
        traceback.print_exc()
