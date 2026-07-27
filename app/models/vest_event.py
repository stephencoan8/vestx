"""
Vest event model for tracking individual vesting events.
"""

from app import db
from datetime import datetime, date
from app.utils.price_utils import get_latest_user_price


class VestEvent(db.Model):
    """Individual vesting event for a grant."""
    
    __tablename__ = 'vest_events'
    
    id = db.Column(db.Integer, primary_key=True)
    grant_id = db.Column(db.Integer, db.ForeignKey('grants.id'), nullable=False, index=True)
    
    # Vest details
    vest_date = db.Column(db.Date, nullable=False)
    shares_vested = db.Column(db.Float, nullable=False)
    # Note: share_price_at_vest is now a @property that calculates dynamically
    
    # Tax handling - simplified flow:
    # 1. User enters cash_paid (cash paid towards taxes)
    # 2. User selects cash_covered_all (did cash cover all taxes?)
    # 3. If not fully covered, user enters shares_sold (shares sold to cover remainder)
    cash_paid = db.Column(db.Float, default=0.0)  # Cash paid towards taxes
    cash_covered_all = db.Column(db.Boolean, default=True)  # Did cash cover all taxes?
    shares_sold = db.Column(db.Float, default=0.0)  # Shares sold to cover remaining taxes
    tax_year = db.Column(db.Integer, nullable=True)  # Tax year for historical rate tracking
    notes = db.Column(db.Text, nullable=True)  # User notes about this vest event
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self) -> str:
        return f'<VestEvent {self.vest_date} - {self.shares_vested} shares>'
    
    @property
    def has_vested(self) -> bool:
        """Check if vest date has passed (based on today's date)."""
        vest_date = self.vest_date
        # Handle both datetime and date objects
        if isinstance(vest_date, datetime):
            vest_date = vest_date.date()
        return vest_date <= date.today()
    
    @property
    def share_price_at_vest(self) -> float:
        """
        Get the stock price at vest date from user's encrypted prices.
        For unvested events (future dates), returns current stock price as estimate.
        For vested events, returns actual historical price at vest date.
        """
        try:
            # For unvested shares, use latest available price (current price)
            if not self.has_vested:
                price = get_latest_user_price(self.grant.user_id)  # Latest price (today or before)
                return price if price is not None else 0.0
            
            # For vested shares, get actual price at vest date
            price = get_latest_user_price(self.grant.user_id, as_of_date=self.vest_date)
            return price if price is not None else 0.0
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error getting share_price_at_vest: {str(e)}", exc_info=True)
            return 0.0
    
    @property
    def value_at_vest(self) -> float:
        """
        Calculate value at vest based on current stock price data.
        For ISOs (stock options): value = shares × (price_at_vest - strike_price)
        For RSUs/RSAs: value = shares × price_at_vest
        For CASH: value = cash amount (shares_vested represents USD amount)
        """
        from app.models.grant import ShareType
        
        # Cash bonuses: shares_vested represents USD amount
        if self.grant.share_type == ShareType.CASH.value:
            return self.shares_vested
        
        price_at_vest = self.share_price_at_vest
        
        # For ISOs, calculate the spread (price at vest - strike price)
        if self.grant.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
            spread = price_at_vest - self.grant.share_price_at_grant
            return self.shares_vested * spread
        
        # For RSUs/RSAs/ESPP, use full price at vest
        return self.shares_vested * price_at_vest
    
    @property
    def shares_withheld_for_taxes(self) -> float:
        """Calculate total shares withheld/sold for taxes."""
        # For cash bonuses, this represents USD withheld
        # New simplified logic: just return shares_sold directly
        # (shares_sold is what user enters when cash didn't cover all taxes)
        return self.shares_sold if self.shares_sold else 0.0
    
    @property
    def shares_received(self) -> float:
        """Calculate actual shares physically received after taxes (or USD for cash bonuses)."""
        return self.shares_vested - self.shares_withheld_for_taxes
    
    @property
    def needs_tax_info(self) -> bool:
        """Check if vested event is missing tax payment information."""
        if not self.has_vested:
            return False
        # ESPP/nqESPP don't need tax info - taxes handled at receipt
        if self.grant.grant_type in ['espp', 'nqespp']:
            return False
        # Needs info if vested but no cash paid recorded (for past vests)
        return self.cash_paid == 0 and self.shares_sold == 0
    
    @property
    def net_value(self) -> float:
        """
        Calculate net value of shares received.
        For ISOs: net_value = shares_received × (price_at_vest - strike_price)
        For RSUs/RSAs: net_value = shares_received × price_at_vest
        For CASH: net_value = USD amount received after taxes
        """
        from app.models.grant import ShareType
        
        # Cash bonuses: shares_received represents USD amount
        if self.grant.share_type == ShareType.CASH.value:
            return self.shares_received
        
        price_at_vest = self.share_price_at_vest
        if not price_at_vest:
            return 0.0
        
        # For ISOs, calculate based on spread
        if self.grant.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
            spread = price_at_vest - self.grant.share_price_at_grant
            return self.shares_received * spread
        
        # For RSUs/RSAs/ESPP, use full price
        return self.shares_received * price_at_vest
    
    @property
    def tax_withheld(self) -> float:
        """
        Calculate total tax withheld (cash paid + value of shares sold).
        For cash bonuses: cash_paid + shares_sold (both in USD)
        For stock grants: cash_paid + (shares_sold × price_at_vest)
        """
        from app.models.grant import ShareType
        
        total_tax = self.cash_paid
        
        # Cash bonuses: shares_sold represents USD amount withheld
        if self.grant.share_type == ShareType.CASH.value:
            total_tax += self.shares_sold
        else:
            # For stock grants: convert shares_sold to USD
            if self.shares_sold > 0:
                total_tax += self.shares_sold * self.share_price_at_vest
        
        return total_tax
    
    def get_comprehensive_tax_breakdown(self, user=None, _tax_profile=None, _annual_incomes=None, _cached_rates=None, _year_income=None) -> dict:
        """
        Get detailed tax breakdown including FICA, Medicare, Social Security.
        Uses user's simplified tax preferences (federal rate, state rate, FICA toggle).

        Args:
            user: Optional User instance to avoid per-call DB lookups (pass current_user
                  from routes when iterating many vests).
        """
        try:
            if user is None:
                user = self.grant.user if self.grant else None
            if not user:
                return {
                    'has_breakdown': False,
                    'gross_value': self.value_at_vest,
                    'total_tax': self.tax_withheld,
                    'net_value': self.net_value
                }

            federal_rate = user.get_federal_tax_rate()
            state_rate = user.get_state_tax_rate()
            include_fica = user.include_fica if user.include_fica is not None else True
            ss_wage_base_maxed = bool(getattr(user, 'ss_wage_base_maxed', False))

            gross_value = self.value_at_vest
            federal_tax = gross_value * federal_rate
            state_tax = gross_value * state_rate
            
            # FICA components (if enabled)
            if include_fica:
                # Social Security: 6.2% up to wage base (skip if already maxed)
                if ss_wage_base_maxed:
                    ss_rate = 0.0
                    social_security_tax = 0
                else:
                    ss_wage_base = 168600
                    ss_rate = 0.062
                    # Simplified: assume this vest pushes user over the cap if gross > wage base
                    if gross_value < ss_wage_base:
                        social_security_tax = gross_value * ss_rate
                    else:
                        social_security_tax = ss_wage_base * ss_rate
                
                # Medicare: 1.45% on all income (always applies)
                medicare_rate = 0.0145
                medicare_tax = gross_value * medicare_rate
                
                # Additional Medicare: 0.9% on income over threshold
                # $200k single, $250k married - simplified to $200k
                additional_medicare_threshold = 200000
                additional_medicare_rate = 0.009
                if gross_value > additional_medicare_threshold:
                    additional_medicare_tax = (gross_value - additional_medicare_threshold) * additional_medicare_rate
                else:
                    additional_medicare_tax = 0
                    additional_medicare_rate = 0.0  # Show 0% if not applicable
            else:
                ss_rate = 0.0
                social_security_tax = 0
                medicare_rate = 0.0
                medicare_tax = 0
                additional_medicare_rate = 0.0
                additional_medicare_tax = 0
            
            # Total FICA
            total_fica = social_security_tax + medicare_tax + additional_medicare_tax
            
            # Total tax
            total_tax = federal_tax + state_tax + total_fica
            net_value = gross_value - total_tax
            
            # Effective rate (for display)
            effective_rate = total_tax / gross_value if gross_value > 0 else 0.0
            
            return {
                'has_breakdown': True,
                'gross_value': gross_value,
                'federal_tax': federal_tax,
                'state_tax': state_tax,
                'social_security_tax': social_security_tax,
                'medicare_tax': medicare_tax,
                'additional_medicare_tax': additional_medicare_tax,
                'total_fica': total_fica,  # Template expects 'total_fica'
                'total_tax': total_tax,
                'net_value': net_value,
                'net_amount': net_value,  # Template also uses 'net_amount' in some places
                'federal_rate': federal_rate,
                'state_rate': state_rate,
                'social_security_rate': ss_rate,
                'medicare_rate': medicare_rate,
                'additional_medicare_rate': additional_medicare_rate,
                'effective_rate': effective_rate,  # Overall tax rate
                'include_fica': include_fica,
                'tax_year': self.tax_year or self.vest_date.year
            }
            
        except Exception as e:
            # Log the error but don't crash
            import logging
            logging.getLogger(__name__).error(f"Error in get_comprehensive_tax_breakdown: {e}")
            # Fallback to basic breakdown
            return {
                'has_breakdown': False,
                'gross_value': self.value_at_vest,
                'total_tax': self.tax_withheld,
                'net_value': self.net_value
            }
    
    def estimate_tax_withholding(self, current_stock_price: float = None,
                                 federal_rate: float = None,
                                 state_rate: float = None,
                                 fica_rate: float = None,
                                 user=None,
                                 _tax_profile=None) -> dict:
        """
        Estimate tax withholding for future vesting events.
        Uses user's simple tax preferences from their profile.

        Args:
            current_stock_price: Stock price to use for estimation (defaults to latest)
            user: Optional User instance to avoid per-call DB lookups
        """
        from app.models.grant import ShareType, GrantType

        if self.has_vested:
            return {
                'tax_amount': self.tax_withheld,
                'is_estimated': False,
                'tax_rate': 0.0
            }

        if current_stock_price is None:
            current_stock_price = get_latest_user_price(self.grant.user_id) or 0.0

        if user is None:
            user = self.grant.user if self.grant else None
        if not user:
            tax_rate = 0.22 + 0.093 + 0.0765
        else:
            tax_rate = user.get_tax_rates()['total']
        
        # Calculate vest value based on grant type
        if self.grant.share_type == ShareType.CASH.value:
            vest_value = self.shares_vested
        elif self.grant.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
            # ISOs: tax on spread (current_price - strike_price)
            spread = current_stock_price - self.grant.share_price_at_grant
            vest_value = self.shares_vested * spread if spread > 0 else 0.0
        elif self.grant.grant_type == GrantType.ESPP.value and self.grant.espp_discount:
            # ESPP: tax on discount portion (ordinary income)
            discount_gain = self.shares_vested * current_stock_price * self.grant.espp_discount
            vest_value = discount_gain
        else:
            # RSUs/RSAs: full value is taxable as ordinary income
            vest_value = self.shares_vested * current_stock_price
        
        # Calculate estimated tax
        estimated_tax = vest_value * tax_rate
        
        return {
            'tax_amount': estimated_tax,
            'is_estimated': True,
            'tax_rate': tax_rate
        }
    
    def get_estimated_sale_tax(self, current_stock_price: float = None,
                               total_sold: float = 0,
                               total_exercised: float = 0,
                               user=None,
                               _tax_profile=None,
                               _annual_incomes=None) -> dict:
        """
        Calculate estimated capital gains tax on remaining shares if sold today.

        Args:
            current_stock_price: Current stock price (defaults to latest user price)
            total_sold: Total shares already sold from this vest
            total_exercised: Total shares already exercised (for ISOs)
            user: Optional User instance to avoid per-call DB lookups
        """
        from app.models.grant import ShareType

        if current_stock_price is None:
            current_stock_price = get_latest_user_price(self.grant.user_id) or 0.0

        shares_held = self.shares_received - total_sold - total_exercised

        if self.grant.share_type == ShareType.CASH.value:
            return {
                'shares_held': shares_held,
                'cost_basis_per_share': 1.0,
                'cost_basis': shares_held,
                'current_value': shares_held,
                'unrealized_gain': 0.0,
                'days_held': 0,
                'is_long_term': False,
                'holding_period': '—',
                'estimated_tax': 0.0,
                'federal_tax': 0.0,
                'niit_tax': 0.0,
                'state_tax': 0.0,
                'federal_rate': 0.0,
                'state_rate': 0.0,
                'method': 'n/a'
            }

        if self.grant.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
            cost_basis_per_share = self.grant.share_price_at_grant
        else:
            cost_basis_per_share = self.share_price_at_vest if self.has_vested else current_stock_price

        cost_basis = shares_held * cost_basis_per_share
        current_value = shares_held * current_stock_price
        unrealized_gain = current_value - cost_basis

        today = date.today()
        days_held = (today - self.vest_date).days if self.has_vested else 0
        is_long_term = days_held >= 365

        if self.has_vested:
            if days_held >= 365:
                years = days_held // 365
                holding_period = f"{years}y {days_held % 365}d"
            else:
                holding_period = f"{days_held}d"
        else:
            holding_period = "—"

        if user is None:
            user = self.grant.user if self.grant else None

        if not user or unrealized_gain <= 0:
            # No user or no gain = no tax
            return {
                'shares_held': shares_held,
                'cost_basis_per_share': cost_basis_per_share,
                'cost_basis': cost_basis,
                'current_value': current_value,
                'unrealized_gain': unrealized_gain,
                'days_held': days_held,
                'is_long_term': is_long_term,
                'holding_period': holding_period,
                'estimated_tax': 0.0,
                'federal_tax': 0.0,
                'niit_tax': 0.0,
                'state_tax': 0.0,
                'federal_rate': 0.0,
                'state_rate': 0.0,
                'method': 'none'
            }
        
        # Use simplified capital gains rates based on holding period
        if is_long_term:
            # Long-term capital gains: typically 0%, 15%, or 20%
            # Use 15% as reasonable default for most users
            federal_rate = 0.15
        else:
            # Short-term capital gains: taxed as ordinary income
            # Use user's federal tax rate
            federal_rate = user.get_federal_tax_rate()
        
        state_rate = user.get_state_tax_rate()
        
        # Calculate taxes
        federal_tax = unrealized_gain * federal_rate
        state_tax = unrealized_gain * state_rate
        
        # NIIT (Net Investment Income Tax): 3.8% on investment income for high earners
        # Applies to single filers with MAGI > $200k, married > $250k
        # Simplified: apply if federal rate is high (proxy for high earner)
        if user.get_federal_tax_rate() >= 0.32:  # Likely high earner
            niit_tax = unrealized_gain * 0.038
        else:
            niit_tax = 0.0
        
        estimated_tax = federal_tax + state_tax + niit_tax
        
        return {
            'shares_held': shares_held,
            'cost_basis_per_share': cost_basis_per_share,
            'cost_basis': cost_basis,
            'current_value': current_value,
            'unrealized_gain': unrealized_gain,
            'days_held': days_held,
            'is_long_term': is_long_term,
            'holding_period': holding_period,
            'estimated_tax': estimated_tax,
            'federal_tax': federal_tax,
            'niit_tax': niit_tax,
            'state_tax': state_tax,
            'federal_rate': federal_rate,
            'state_rate': state_rate,
            'method': 'simplified'
        }
    
    def get_complete_data(self, user_key: bytes, current_price: float = None,
                         sales_data=None, exercises_data=None, user=None,
                         tax_profile=None, annual_incomes=None) -> dict:
        """
        Single source of truth for vest event presentation data.

        Args:
            user_key: Decrypted user key for price decryption
            current_price: Current stock price (optional; fetched if not provided)
            sales_data: List of StockSale objects for this vest
            exercises_data: List of ISOExercise objects for this vest
            user: Optional User instance to avoid extra DB lookups
        """
        import logging
        logger = logging.getLogger(__name__)

        has_vested = False
        is_iso = False
        is_cash = False

        try:
            from app.models.grant import ShareType

            if not user_key:
                user_key = b''

            if not self.grant:
                raise ValueError(f"VestEvent {self.id} has no associated grant")

            today = date.today()
            has_vested = self.vest_date <= today if self.vest_date else False
            is_iso = self.grant.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value] if self.grant.share_type else False
            is_cash = self.grant.share_type == ShareType.CASH.value if self.grant.share_type else False

            # Prefer centralized (request-cached) price helper
            as_of = self.vest_date if has_vested else today
            price_at_vest = get_latest_user_price(self.grant.user_id, as_of_date=as_of) or 0.0

            if current_price is None:
                current_price = get_latest_user_price(self.grant.user_id) or 0.0

            strike_price = self.grant.share_price_at_grant if is_iso else None

            shares_vested = self.shares_vested or 0.0
            shares_sold_for_tax = self.shares_sold or 0.0
            cash_paid = self.cash_paid or 0.0
            spread = 0.0

            if is_cash:
                gross_value = shares_vested
                shares_withheld = shares_sold_for_tax
                tax_withheld_value = cash_paid + shares_withheld
            elif is_iso:
                if strike_price is None:
                    strike_price = 0.0
                spread = price_at_vest - strike_price
                gross_value = shares_vested * spread
                shares_withheld = shares_sold_for_tax
                tax_withheld_value = cash_paid + (shares_withheld * price_at_vest)
            else:
                gross_value = shares_vested * price_at_vest
                shares_withheld = shares_sold_for_tax
                tax_withheld_value = cash_paid + (shares_withheld * price_at_vest)

            shares_received = shares_vested - shares_withheld

            if is_cash:
                net_value = shares_received
            elif is_iso:
                net_value = shares_received * spread
            else:
                net_value = shares_received * price_at_vest

            total_sold = sum(s.shares_sold for s in sales_data) if sales_data else 0
            total_exercised = sum(e.shares_exercised for e in exercises_data) if exercises_data else 0
            remaining_shares = shares_received - total_sold - total_exercised

            if is_cash:
                cost_basis_per_share = 1.0
            elif is_iso:
                cost_basis_per_share = strike_price if strike_price is not None else 0.0
            else:
                cost_basis_per_share = price_at_vest if has_vested else current_price

            tax_breakdown = None
            if not is_cash:
                try:
                    tax_breakdown = self.get_comprehensive_tax_breakdown(user=user)
                except Exception as e:
                    logger.error("Error getting tax breakdown: %s", e)

            sale_tax_projection = None
            if remaining_shares > 0 and not is_cash:
                try:
                    sale_tax_projection = self.get_estimated_sale_tax(
                        current_stock_price=current_price,
                        total_sold=total_sold,
                        total_exercised=total_exercised,
                        user=user,
                    )
                except Exception as e:
                    logger.error("Error getting sale tax projection: %s", e)

            return {
                'vest_id': self.id,
                'vest_date': self.vest_date,
                'has_vested': has_vested,
                'is_iso': is_iso,
                'is_cash': is_cash,
                'grant_type': self.grant.grant_type,
                'share_type': self.grant.share_type,
                'shares_vested': self.shares_vested,
                'shares_withheld_for_taxes': shares_withheld,
                'shares_received': shares_received,
                'shares_sold': total_sold,
                'shares_exercised': total_exercised,
                'shares_remaining': remaining_shares,
                'price_at_vest': price_at_vest,
                'current_price': current_price,
                'strike_price': strike_price,
                'cost_basis_per_share': cost_basis_per_share,
                'gross_value': gross_value,
                'tax_withheld_value': tax_withheld_value,
                'net_value': net_value,
                'current_market_value': remaining_shares * current_price if not is_cash else remaining_shares,
                'total_cost_basis': remaining_shares * cost_basis_per_share,
                'unrealized_gain': (
                    (remaining_shares * current_price) - (remaining_shares * cost_basis_per_share)
                    if not is_cash else 0
                ),
                'cash_paid': self.cash_paid,
                'cash_covered_all': self.cash_covered_all,
                'tax_breakdown': tax_breakdown,
                'sale_tax_projection': sale_tax_projection,
                'notes': self.notes,
                'needs_tax_info': self.needs_tax_info,
            }
        except Exception as e:
            logger.error("Error calculating vest data in get_complete_data: %s", e, exc_info=True)
            return {
                'vest_id': self.id,
                'vest_date': self.vest_date if hasattr(self, 'vest_date') else None,
                'has_vested': has_vested,
                'is_iso': is_iso,
                'is_cash': is_cash,
                'grant_type': self.grant.grant_type if self.grant else None,
                'share_type': self.grant.share_type if self.grant else None,
                'shares_vested': self.shares_vested or 0.0,
                'shares_withheld_for_taxes': 0.0,
                'price_at_vest': 0.0,
                'gross_value': 0.0,
                'tax_withheld_value': 0.0,
                'shares_received': self.shares_received or 0.0,
                'net_value': 0.0,
                'current_price': 0.0,
                'current_market_value': 0.0,
                'total_cost_basis': 0.0,
                'unrealized_gain': 0.0,
                'strike_price': self.grant.share_price_at_grant if self.grant and hasattr(self.grant, 'share_price_at_grant') else None,
                'cost_basis_per_share': 0.0,
                'shares_sold': 0.0,
                'shares_exercised': 0.0,
                'shares_remaining': self.shares_received or 0.0,
                'tax_breakdown': None,
                'sale_tax_projection': None,
                'cash_paid': self.cash_paid or 0.0,
                'cash_covered_all': self.cash_covered_all or False,
                'notes': self.notes if hasattr(self, 'notes') else '',
                'needs_tax_info': self.needs_tax_info if hasattr(self, 'needs_tax_info') else False,
                'error': str(e),
            }