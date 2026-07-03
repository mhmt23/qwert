// SPDX-License-Identifier: MIT
pragma solidity 0.7.6; // KASITLI: <0.8, overflow koruması yok

// UYARI: Bu kontrat EĞİTİM amaçlıdır ve KASITLI olarak açıklıdır.
// Tarayıcıyı doğrulamak ve elle inceleme pratiği yapmak için fixture'dır.
// Üretimde ASLA kullanma.

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address who) external view returns (uint256);
}

interface IUniswapV2Pair {
    function getReserves() external view returns (uint112, uint112, uint32);
}

contract VulnerableLendingPool {
    address public owner;
    IERC20 public asset;
    IUniswapV2Pair public priceSource;

    mapping(address => uint256) public collateral;
    mapping(address => uint256) public debt;
    mapping(address => uint256) public shares;
    uint256 public totalShares;

    constructor(address _asset, address _pair) {
        owner = msg.sender;
        asset = IERC20(_asset);
        priceSource = IUniswapV2Pair(_pair);
    }

    // AÇIK (Katman 4): Anlık rezervden fiyat — flash-loan ile manipüle edilebilir
    function getPrice() public view returns (uint256) {
        (uint112 r0, uint112 r1, ) = priceSource.getReserves();
        return uint256(r1) / uint256(r0);
    }

    // AÇIK (Katman 5): İlk-depozitör share inflation koruması yok
    function deposit(uint256 amount) external {
        asset.transferFrom(msg.sender, address(this), amount);
        uint256 minted = totalShares == 0 ? amount : (amount * totalShares) / asset.balanceOf(address(this));
        shares[msg.sender] += minted;
        totalShares += minted;
    }

    // AÇIK (Katman 3): Dış çağrıdan SONRA durum güncelleniyor, guard yok
    function withdraw(uint256 amount) external {
        require(collateral[msg.sender] >= amount, "yetersiz");
        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "transfer failed");
        collateral[msg.sender] -= amount;
    }

    // AÇIK (Katman 5): Likidasyonda sağlık faktörü kontrolü yok
    function liquidate(address user) external {
        uint256 seized = collateral[user];
        collateral[user] = 0;
        debt[user] = 0;
        asset.transfer(msg.sender, seized);
    }

    // AÇIK (Katman 2): çarpmadan önce bölme → precision loss
    function accrueInterest(uint256 principal, uint256 rate) public pure returns (uint256) {
        return principal / 10000 * rate;
    }

    // AÇIK (Katman 1): erişim kontrolü yok, herkes owner'ı değiştirebilir
    function setOwner(address newOwner) public {
        owner = newOwner;
    }

    // AÇIK (Katman 1): ayrıcalıklı sweep, modifier yok
    function rescueTokens(address token, uint256 amount) external {
        IERC20(token).transfer(msg.sender, amount);
    }
}
